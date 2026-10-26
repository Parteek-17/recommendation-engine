"""Step 2 — Evaluation harness: how good are the recommendations? Writes reco/results.md.

    python -m reco.evaluate

The idea (an "offline backtest"): pretend it is the end of the training period, make
top-10 lists for every user, then check them against what users REALLY did afterwards.

Accuracy metrics (higher = better), per user then averaged:
- Recall@10 : of the products the user engaged with later, how many were in our top 10?
              (divided by min(#relevant, 10), so a perfect list always scores 1.0)
- NDCG@10   : like recall, but a hit at position 1 counts more than a hit at position 10.
- MAP@10    : mean average precision — rewards putting ALL the hits near the top.
Beyond accuracy:
- Coverage  : share of the catalog that appears in at least one user's list.
- Diversity : distinct categories per list ÷ list length (1.0 = every item from a new category).
- Novelty   : how "unknown" the recommended items are (−log2 of popularity share; higher = less obvious).
Ground truth checks (synthetic/sample data only — evaluation, NEVER a model input):
- Pref-cat  : % of recommended items in the user's hidden preferred categories.
- Sub-leak  : % of recommended items that are substitutes of something the user already
              owns (lower = better post-purchase intelligence).
- Complement recall: % of true complement pairs (phone -> cover) found by get_complements.

Protocol:
- Validation table: models trained ≤ train_end, scored on the validation window (for tuning).
- Test table: models trained ≤ valid_end; the ranker was trained on windows BEFORE the test
  window; scored on the untouched test window. This is the honest headline number.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict

import numpy as np
import pandas as pd

from common.config import PROJECT_ROOT, settings
from common.db import read_ground_truth
from common.logging_utils import get_logger, now_iso
from reco.candidates.merge import merge_candidates
from reco.data import (RecoData, all_histories, isin, load_data, make_split, relevance_labels,
                       weighted_interactions, window)
from reco.engine import Recommender
from reco.ranker import train_ranker
from reco.rerank import RULES

log = get_logger(__name__)

K = 10
MAX_EVAL_USERS = 1000  # big data: score a fixed random sample of users (NDCG std. error ≈ 0.01)
RESULTS_PATH = PROJECT_ROOT / "reco" / "results.md"
STAGE_LABELS = {
    "popularity": "Popularity (baseline)",
    "item_cf": "Item-item CF",
    "als": "ALS",
    "content": "Content (TF-IDF)",
    "merged": "Merged candidates (blend)",
    "ranker": "LightGBM ranker",
    "full": "Full pipeline (ranker + rules)",
}

# ---------------------------------------------------------------------------
# Metric functions (one user's list)
# ---------------------------------------------------------------------------


def recall_at_k(recs: list[str], relevant: set[str], k: int = K) -> float:
    if not relevant:
        return 0.0
    return len(set(recs[:k]) & relevant) / min(len(relevant), k)


def ndcg_at_k(recs: list[str], relevant: set[str], k: int = K) -> float:
    dcg = sum(1 / math.log2(i + 2) for i, p in enumerate(recs[:k]) if p in relevant)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    return dcg / ideal if ideal else 0.0


def average_precision_at_k(recs: list[str], relevant: set[str], k: int = K) -> float:
    hits, total = 0, 0.0
    for i, p in enumerate(recs[:k]):
        if p in relevant:
            hits += 1
            total += hits / (i + 1)
    return total / min(len(relevant), k) if relevant else 0.0


# ---------------------------------------------------------------------------
# Ground truth helpers
# ---------------------------------------------------------------------------


class GroundTruth:
    """Hidden truth from the data generator. Only ever used to SCORE, never to recommend."""

    def __init__(self):
        profiles = read_ground_truth("user_hidden_profile")
        relations = read_ground_truth("product_relations")
        self.available = profiles is not None and relations is not None
        if not self.available:
            return
        self.preferred = {u: set(str(c).split("|")) for u, c in zip(profiles["user_id"], profiles["preferred_categories"])}
        self.substitutes: dict[str, set[str]] = defaultdict(set)
        self.complements: dict[str, set[str]] = defaultdict(set)
        for a, b, rel in zip(relations["product_id"], relations["related_product_id"], relations["relation"]):
            (self.substitutes if rel == "substitute" else self.complements)[a].add(b)


# ---------------------------------------------------------------------------
# Running the stages
# ---------------------------------------------------------------------------


def _sample_users(relevant: dict, max_users: int) -> dict:
    """Big datasets: score a fixed random sample of users (same sample every run)."""
    if len(relevant) <= max_users:
        return relevant
    rng = np.random.default_rng(42)
    keep = rng.choice(sorted(relevant), size=max_users, replace=False)
    return {u: relevant[u] for u in keep}


def evaluate_stages(engine: Recommender, data_past: RecoData, labels: pd.DataFrame, stages: list[str],
                    gt: GroundTruth | None) -> tuple[dict[str, dict[str, float]], int]:
    """Score every stage on every user that has labels. Returns ({stage: metrics}, n_users).

    A stage named "full-<rule>" = the full pipeline with that re-ranking rule switched off (ablation);
    "full-<rule1>+<rule2>" switches off several.
    """
    relevant = _sample_users(labels.groupby("user_id")["product_id"].apply(set).to_dict(), MAX_EVAL_USERS)
    histories = all_histories(data_past, list(relevant))
    train_pop = weighted_interactions(data_past.events, personal=False).groupby("product_id")["weight"].sum()
    total = float(train_pop.sum()) + len(engine.items)
    item_cat = engine.items["category_id"].to_dict()
    consumable = engine.items["is_consumable"].fillna(0).to_dict()

    rows: dict[str, dict[str, list]] = {s: defaultdict(list) for s in stages}
    shown: dict[str, set] = {s: set() for s in stages}
    for uid, rel in relevant.items():
        hist = histories[uid]
        cands = merge_candidates(engine, hist)  # computed once, reused by every stage
        for stage in stages:
            base, _, rule = stage.partition("-")
            frame = engine.recommend_frame(hist, K, base, disabled_rules=frozenset(rule.split("+")) - {""},
                                           cands=cands)
            recs = frame["product_id"].tolist()
            shown[stage].update(recs)
            m = rows[stage]
            m["recall"].append(recall_at_k(recs, rel))
            m["ndcg"].append(ndcg_at_k(recs, rel))
            m["map"].append(average_precision_at_k(recs, rel))
            cats = [item_cat.get(p) for p in recs]
            m["diversity"].append(len({c for c in cats if isinstance(c, str)}) / max(len(recs), 1))
            m["novelty"].append(np.mean([-math.log2((train_pop.get(p, 0) + 1) / total) for p in recs])
                                if recs else 0.0)
            if gt is not None and gt.available:
                pref = gt.preferred.get(uid, set())
                m["pref_cat"].append(np.mean([c in pref for c in cats]) if recs else 0.0)
                owned = [p for p in hist.purchases if consumable.get(p, 0) != 1]
                leak = [p for p in recs if any(p == o or p in gt.substitutes.get(o, ()) for o in owned)]
                m["sub_leak"].append(len(leak) / max(len(recs), 1))

    results = {}
    for stage in stages:
        metrics = {name: float(np.mean(vals)) for name, vals in rows[stage].items()}
        metrics["coverage"] = len(shown[stage]) / max(len(engine.items), 1)
        results[stage] = metrics
    return results, len(relevant)


def complement_metrics(engine: Recommender, gt: GroundTruth) -> dict[str, float]:
    """How well do get_complements / get_similar recover the planted product relations?"""
    found_pairs = total_pairs = returned = sub_in_comp = 0
    sim_returned = sim_subs = 0
    for anchor, comps in gt.complements.items():
        recs = [r.product_id for r in engine.complements(anchor, K)]
        found_pairs += len(set(recs) & comps)
        total_pairs += len(comps)
        returned += len(recs)
        sub_in_comp += len(set(recs) & gt.substitutes.get(anchor, set()))
    for anchor, subs in gt.substitutes.items():
        recs = [r.product_id for r in engine.similar(anchor, K)]
        sim_returned += len(recs)
        sim_subs += len(set(recs) & subs)
    return {
        "complement_recall": found_pairs / max(total_pairs, 1),
        "complement_precision": found_pairs / max(returned, 1),
        "substitutes_in_complements": sub_in_comp / max(returned, 1),
        "similar_are_substitutes": sim_subs / max(sim_returned, 1),
    }


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------


def _table(results: dict[str, dict[str, float]], with_gt: bool) -> str:
    base = results.get("popularity", {}).get("ndcg", 0)
    cols = ["Recall@10", "NDCG@10", "MAP@10", "NDCG vs baseline", "Coverage", "Diversity", "Novelty"]
    if with_gt:
        cols += ["Pref-cat %", "Sub-leak %"]
    lines = ["| Stage | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    for stage, m in results.items():
        lift = f"{m['ndcg'] / base:.2f}×" if base > 0 else "n/a"
        cells = [f"{m['recall']:.3f}", f"{m['ndcg']:.3f}", f"{m['map']:.3f}", lift,
                 f"{m['coverage']:.0%}", f"{m['diversity']:.2f}", f"{m['novelty']:.1f}"]
        if with_gt:
            cells += [f"{m.get('pref_cat', 0):.0%}", f"{m.get('sub_leak', 0):.1%}"]
        lines.append(f"| {STAGE_LABELS[stage]} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


# For a user with no history this = plain popularity minus out-of-stock items (ranker and personal
# rules don't apply to them), i.e. the fair baseline for the cold-start table.
IN_STOCK_POPULARITY = "full-" + "+".join(r for r in RULES if r != "stock")
STAGE_LABELS[IN_STOCK_POPULARITY] = "Popularity, in stock only"

RULE_LABELS = {
    "stock": "Remove out-of-stock", "minors": "Hide age-restricted for minors",
    "owned": "Hide already-bought items (+ their substitutes)", "substitutes": "Hide substitutes of bought items",
    "complements_restock": "Add complements + restock reminders", "budget": "Push down over-budget items",
    "diversity": "Max 3 per brand/category", "long_tail": "20% long-tail slots",
}


def _ablation_section(full: dict[str, float], ablation: dict[str, dict[str, float]], with_gt: bool) -> list[str]:
    """'What does each rule cost or gain?' — full pipeline with ONE rule switched off at a time."""
    head = "| Rule switched OFF | NDCG@10 | Δ NDCG | Coverage | Diversity |" + (" Sub-leak % |" if with_gt else "")
    lines = ["## Re-ranking rules — what each one costs or gains (test window)", "",
             "Each row = the full pipeline with ONE rule switched off. Δ NDCG > 0 means the rule costs accuracy "
             "(often on purpose: e.g. users re-view products they already bought, but recommending those is useless).",
             "", head, "|---|---|---|---|---|" + ("---|" if with_gt else ""),
             f"| _(none — all rules on)_ | {full['ndcg']:.3f} | — | {full['coverage']:.0%} | {full['diversity']:.2f} |"
             + (f" {full.get('sub_leak', 0):.1%} |" if with_gt else "")]
    for rule, m in ablation.items():
        row = (f"| {RULE_LABELS.get(rule, rule)} | {m['ndcg']:.3f} | {m['ndcg'] - full['ndcg']:+.3f} | "
               f"{m['coverage']:.0%} | {m['diversity']:.2f} |")
        lines.append(row + (f" {m.get('sub_leak', 0):.1%} |" if with_gt else ""))
    return lines + [""]


def run(db_path=None, out_path=RESULTS_PATH) -> str:
    start = time.time()
    data = load_data(db_path)
    if data.events.empty:
        raise RuntimeError("No events found. Run `python scripts/init_db.py` first.")
    split = make_split(data.events)
    gt = GroundTruth() if data.has("ground_truth") else None
    with_gt = gt is not None and gt.available
    candidate_stages = [s for s in ("popularity", "item_cf", "als", "content", "merged")]

    # --- validation: fit on train, score validation window
    past = data.until(split.train_end)
    engine_val = Recommender.fit(past)
    stages_val = [s for s in candidate_stages if s in ("popularity", "merged") or getattr(engine_val, s) is not None]
    val_labels = relevance_labels(window(data.events, split.valid_start, split.valid_end))
    val_results, n_val = evaluate_stages(engine_val, past, val_labels, stages_val, gt)

    # --- test: fit on train+validation, ranker trained on earlier windows
    past = data.until(split.valid_end)
    engine_test = Recommender.fit(past)
    engine_test.ranker = train_ranker(data, split)
    stages_test = [s for s in stages_val] + (["ranker"] if engine_test.ranker else []) + ["full"]
    ablation_stages = [f"full-{rule}" for rule in RULES]
    test_labels = relevance_labels(window(data.events, split.test_start, split.test_end))
    test_results, n_test = evaluate_stages(engine_test, past, test_labels, stages_test + ablation_stages, gt)
    ablation = {s.split("-", 1)[1]: test_results.pop(s) for s in ablation_stages}

    # cold-start users in the test window (no history before it)
    known = set(past.events["user_id"])
    cold_labels = test_labels[~isin(test_labels["user_id"], known)]
    cold_results, n_cold = ({}, 0)
    if not cold_labels.empty:
        cold_results, n_cold = evaluate_stages(engine_test, past, cold_labels,
                                               ["popularity", IN_STOCK_POPULARITY, "full"], gt)

    md = [
        "# Recommendation engine — evaluation results",
        "",
        f"_Generated {now_iso()} by `python -m reco.evaluate` in {time.time() - start:.0f}s._",
        "",
        f"- **Data:** `DATA_SOURCE={settings.DATA_SOURCE}` (level {data.level}) — {len(data.events):,} events, "
        f"{data.events['user_id'].nunique():,} users, {len(engine_test.items):,} products",
        f"- **Split:** train ≤ {split.train_end} · validation {split.valid_start}…{split.valid_end} · "
        f"test {split.test_start}…{split.test_end}",
        f"- **Relevant items** = anything the user viewed/clicked/wishlisted/carted/bought in the window. "
        f"Recall is divided by min(#relevant, 10).",
        f"- ALS backend: `{engine_test.als.backend if engine_test.als else 'off'}` · "
        f"ranker: {'trained on ' + str(engine_test.ranker.n_users) + ' user-windows' if engine_test.ranker else 'not trained (too little data) — full pipeline uses the blend'}",
        "",
        f"## Test window — headline ({n_test} users)",
        "",
        "Models trained on everything up to the end of validation; scored on the untouched test window.",
        "",
        _table(test_results, with_gt),
        "",
        f"## Validation window ({n_val} users)",
        "",
        "Candidate generators trained ≤ train end; used for tuning.",
        "",
        _table(val_results, with_gt),
        "",
    ]
    md += _ablation_section(test_results["full"], ablation, with_gt)
    if n_cold:
        md += [f"## Cold-start users in the test window ({n_cold} users with no earlier history)", "",
               "New users click sold-out bestsellers too, and plain popularity gets credit for showing them — but "
               "the store can't sell those. **In-stock popularity** is the fair yardstick for the full pipeline.",
               "", _table(cold_results, with_gt), ""]
    if with_gt:
        cm = complement_metrics(engine_test, gt)
        md += [
            "## Product relations vs. ground truth",
            "",
            "| Check | Value | Meaning |",
            "|---|---|---|",
            f"| Complement recall@10 | {cm['complement_recall']:.0%} | true complement pairs found by `get_complements` |",
            f"| Complement precision@10 | {cm['complement_precision']:.0%} | returned complements that are true complements |",
            f"| Substitutes in complements | {cm['substitutes_in_complements']:.1%} | should be ~0 (a phone is not a phone's complement) |",
            f"| `get_similar` are substitutes | {cm['similar_are_substitutes']:.0%} | similar items that are true substitutes |",
            "",
        ]
    if engine_test.ranker is not None:
        imp = list(engine_test.ranker.importance().items())[:8]
        md += ["## Ranker — most-used features", "", "| Feature | Splits |", "|---|---|"]
        md += [f"| {k} | {v} |" for k, v in imp] + [""]
    md += [
        "## How to read this",
        "",
        "- **NDCG vs baseline** > 1.00× means the stage beats plain popularity.",
        "- The full pipeline trades a little raw accuracy for business rules: no out-of-stock items, no "
        "substitutes of things already bought (see Sub-leak), budget fit, max 3 per brand/category, "
        "~20% long-tail slots (see Coverage/Novelty).",
    ]
    if n_test < 200:
        md.append(f"- Only {n_test} test users: numbers are noisy — re-run on `DATA_SOURCE=synthetic` "
                  "for meaningful comparisons.")
    elif n_test == MAX_EVAL_USERS:
        md.append(f"- Scored on a fixed random sample of {MAX_EVAL_USERS:,} users per window "
                  "(NDCG standard error ≈ 0.01).")
    md += [
        "- Ground truth (hidden preferences, product relations) is used ONLY here, never as model input.",
        "",
    ]
    text = "\n".join(md)
    out_path.write_text(text, encoding="utf-8")
    return text


def main() -> None:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Backtest the recommender and write a markdown report.")
    parser.add_argument("--out", type=Path, default=RESULTS_PATH, help="where to write the report")
    args = parser.parse_args()
    text = run(out_path=args.out)
    print(text)
    print(f"\nSaved to {args.out}")


if __name__ == "__main__":
    main()
