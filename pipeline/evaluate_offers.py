"""Offline evaluation of the offer side: strategies, guardrails, pipeline funnel.

    python -m pipeline.evaluate_offers                     # -> pipeline/offer_eval.md (+ .json)
    python -m pipeline.evaluate_offers --max-triggers 800  # faster

Everything runs on a TEMPORARY COPY of app.db, so the real database is never changed
(the generator saves offers and the guardrails write audit rows while we evaluate).

1. Triggers at the end of the data window (default 2026-09-30 19:00) with detect_triggers().
   We evaluate at the END so no model ever sees "future" events.
2. For each (user, trigger): candidate offers = the Offer Engine's personal offers + the live
   starter offers for that trigger, ranked by the rec engine, filtered by the real guardrails.
3. Strategies compared on the SAME guardrail-safe candidates:
     engine        = what the pipeline sends (rec-engine rank #1)
     random        = a random safe offer
     max_discount  = the safe offer that gives away the most ₹
     generic       = best store-wide/category starter offer only (no personal offers)
4. Response model = the one planted in the synthetic data (data_gen/offers.py, evaluation only):
     P(redeem) = open 0.60 x click 0.25 x min(0.95, 0.10 x (1 + 3 x discount x price_sensitivity) x 1.5 if
                 the product is in the user's hidden favourite categories)
   and 20% of redemptions would have happened anyway (holdout), so incremental = 0.8 x redemptions.
   An offer only counts if it covers the product the trigger is about (same rule as the simulator).
5. Pipeline funnel: link a sample of opted-in users to a fake Telegram chat and run one
   scheduler round in dry-run mode -> how many runs stop at which step.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import shutil
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd

from common.config import PROJECT_ROOT, settings
from common.db import get_connection, read_ground_truth
from common.logging_utils import get_logger

log = get_logger(__name__)

OUT_MD = PROJECT_ROOT / "pipeline" / "offer_eval.md"
DEFAULT_NOW = "2026-09-30T19:00:00"
OPEN_RATE, CLICK_OF_OPENED = 0.60, 0.25            # data_gen/config.py
REDEEM_BASE, REDEEM_DISCOUNT_FACTOR, REDEEM_TASTE_MULT = 0.10, 3.0, 1.5
WOULD_BUY_ANYWAY = 0.20
STRATEGIES = ["engine", "random", "max_discount", "generic"]


# ---------------------------------------------------------------------------
# Response oracle (ground truth -> evaluation only)
# ---------------------------------------------------------------------------


class Oracle:
    def __init__(self, db_path: Path):
        hidden = read_ground_truth("user_hidden_profile")
        if hidden is None:
            raise SystemExit("No ground truth for this DATA_SOURCE - the offer evaluation needs synthetic data.")
        self.sens = dict(zip(hidden["user_id"], hidden["price_sensitivity"].astype(float)))
        self.prefers = {u: set(c.split("|")) for u, c in zip(hidden["user_id"], hidden["preferred_categories"])}
        with get_connection(db_path) as conn:
            prod = pd.read_sql_query("SELECT product_id, price, category_id FROM products", conn)
            cats = pd.read_sql_query("SELECT category_id, parent_id FROM categories", conn)
        self.price = dict(zip(prod["product_id"], prod["price"].astype(float)))
        self.cat = dict(zip(prod["product_id"], prod["category_id"]))
        self.parent = dict(zip(cats["category_id"], cats["parent_id"]))

    def in_category(self, product_id: str, category_id: str) -> bool:
        leaf = self.cat.get(product_id)
        return leaf == category_id or self.parent.get(leaf) == category_id

    def goal(self, offer, target: str | None) -> str | None:
        """The product this offer would actually be used on (None = doesn't fit the trigger)."""
        if offer.offer_type.value == "bundle":
            return offer.bundle_product_id
        if offer.product_id:
            return offer.product_id if target in (None, offer.product_id) else None
        if offer.category_id:
            return target if target and self.in_category(target, offer.category_id) else None
        return target  # whole-cart offer: used on whatever the user wanted

    def discount_fraction(self, offer, product: str) -> float:
        v, t, price = float(offer.value), offer.offer_type.value, max(1.0, self.price.get(product, 1.0))
        if t in ("pct_off", "bundle", "buy_again"):
            return v / 100
        if t == "flat_off":
            return min(1.0, v / price)
        if t == "points":
            return min(0.10, v * 0.25 / price)
        return 0.05  # free delivery

    def outcome(self, user_id: str, offer, target: str | None) -> tuple[float, float]:
        """(P(redeem), expected ₹ discount cost) for one sent offer."""
        product = self.goal(offer, target)
        if product is None or product not in self.price or self.price[product] < float(offer.min_cart_value or 0):
            return 0.0, 0.0
        disc = self.discount_fraction(offer, product)
        taste = REDEEM_TASTE_MULT if self.cat.get(product) in self.prefers.get(user_id, set()) else 1.0
        p_click_redeem = min(0.95, REDEEM_BASE * (1 + REDEEM_DISCOUNT_FACTOR * disc * self.sens.get(user_id, 0.5)) * taste)
        p = OPEN_RATE * CLICK_OF_OPENED * p_click_redeem
        cost = 49.0 if offer.offer_type.value == "free_delivery" else disc * self.price[product]
        return p, p * cost


def trigger_target(user_id: str, trigger: str, now: datetime, db_path: Path) -> str | None:
    """The product the trigger is about (cart item, wishlist item, due consumable, last purchase)."""
    stamp = now.isoformat(timespec="seconds")
    event = {"cart_abandon": "add_to_cart", "price_drop": "wishlist", "repurchase_due": "purchase",
             "post_purchase": "purchase"}.get(trigger)
    with get_connection(db_path) as conn:
        if event is None:  # win_back: the product this user cared about most (their favourite kind of thing)
            row = conn.execute("SELECT product_id FROM events WHERE user_id = ? AND timestamp <= ? "
                               "GROUP BY product_id ORDER BY COUNT(*) DESC LIMIT 1", (user_id, stamp)).fetchone()
            return row["product_id"] if row else None
        row = conn.execute("SELECT product_id FROM events WHERE user_id = ? AND event_type = ? AND timestamp <= ? "
                           "ORDER BY timestamp DESC LIMIT 1", (user_id, event, stamp)).fetchone()
    return row["product_id"] if row else None


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------


def evaluate(now: datetime, max_triggers: int, funnel_users: int, seed: int, only: str | None = None) -> dict:
    from offers import apply_offer_guardrails
    from offers.generator import generate_offers
    from offers.rules import offer_value_inr
    from offers.segments import list_live_offers
    from offers.store import Catalog
    from pipeline.triggers import detect_triggers
    from reco import rank_offers_for_user

    rng = random.Random(seed)
    tmp = Path(tempfile.mkdtemp(prefix="offer_eval_"))
    db = tmp / "eval.db"
    log.info("Copying app.db to a temp file (the real DB is never touched)...")
    shutil.copy(settings.DB_PATH, db)
    settings.DB_PATH = db  # everything below (offers, reco history, audit) now uses the copy
    with get_connection(db) as conn:
        conn.execute("DELETE FROM audit_log")

    oracle = Oracle(db)
    catalog = Catalog.load(db)
    pairs = [p for p in detect_triggers(now) if p[1] != "festival" and (only is None or p[1] == only)]
    trigger_counts = Counter(t for _, t in pairs)
    rng.shuffle(pairs)
    pairs = pairs[:max_triggers]
    log.info("Evaluating %d trigger(s)...", len(pairs))

    totals = {s: Counter() for s in STRATEGIES}
    by_trigger = {}
    guardrail_in = guardrail_out = 0
    for i, (user_id, trigger) in enumerate(pairs, 1):
        if i % 100 == 0:
            log.info("  %d/%d", i, len(pairs))
        personal = generate_offers(trigger, user_id, now=now)
        live = list_live_offers(user_id, trigger, now=now)
        offers = {o.offer_id: o for o in live + personal}
        if not offers:
            continue
        ranked = rank_offers_for_user(user_id, list(offers.values()))
        safe = apply_offer_guardrails(user_id, ranked, now=now)
        guardrail_in += len(ranked)
        guardrail_out += len(safe)
        if not safe:
            continue
        safe_offers = [offers[r.offer_id] for r in safe]
        generic = [offers[r.offer_id] for r in safe if not str(offers[r.offer_id].segment).startswith("user:")]
        picks = {
            "engine": safe_offers[0],
            "random": rng.choice(safe_offers),
            "max_discount": max(safe_offers, key=lambda o: offer_value_inr(o, catalog)),
            "generic": generic[0] if generic else None,
        }
        target = trigger_target(user_id, trigger, now, db)
        for strategy, offer in picks.items():
            row = by_trigger.setdefault((trigger, strategy), Counter())
            for bucket in (totals[strategy], row):
                bucket["triggers"] += 1
                if offer is None:
                    continue
                p, cost = oracle.outcome(user_id, offer, target)
                bucket["sent"] += 1
                bucket["redeem"] += p
                bucket["cost"] += cost

    guardrails = audit_counts(db, "offer_guardrail")
    funnel = run_funnel(now, funnel_users, rng) if funnel_users else {"linked_users": 0, "statuses": {}, "eligibility_blocks": {}}
    shutil.rmtree(tmp, ignore_errors=True)
    return {
        "now": now.isoformat(), "triggers_found": dict(trigger_counts), "evaluated": len(pairs),
        "strategies": {s: summarize(c) for s, c in totals.items()},
        "by_trigger": {f"{t}|{s}": summarize(c) for (t, s), c in sorted(by_trigger.items())},
        "guardrails": {"ranked_in": guardrail_in, "kept": guardrail_out, "blocked_reasons": guardrails},
        "funnel": funnel,
    }


def summarize(c: Counter) -> dict:
    n, redeem, cost = c["triggers"], c["redeem"], c["cost"]
    return {"triggers": n, "sent": c["sent"],
            "redeem_per_1000": round(1000 * redeem / n, 2) if n else 0.0,
            "incremental_per_1000": round(1000 * redeem * (1 - WOULD_BUY_ANYWAY) / n, 2) if n else 0.0,
            "cost_per_1000_inr": round(1000 * cost / n, 0) if n else 0.0,
            "cost_per_incremental_inr": round(cost / (redeem * (1 - WOULD_BUY_ANYWAY)), 0) if redeem else None}


def audit_counts(db: Path, action: str) -> dict:
    """Blocked reasons, grouped by their first words (e.g. 'would stack with OF0012' -> 'would stack with')."""
    with get_connection(db) as conn:
        rows = conn.execute("SELECT reason FROM audit_log WHERE action = ? AND decision = 'blocked'", (action,)).fetchall()
    groups = Counter()
    for r in rows:
        reason = r["reason"] or ""
        for prefix in ("would stack with", "discount", "daily discount budget", "segment", "already sent",
                       "offer is", "needs admin approval", "age-restricted", "used", "user is not in"):
            if reason.startswith(prefix):
                reason = prefix
                break
        groups[reason[:60]] += 1
    return dict(groups.most_common())


def run_funnel(now: datetime, n_users: int, rng: random.Random) -> dict:
    """Link a sample of opted-in users (fake chat ids), run one DRY-RUN scheduler round at `now`.
    The Offers API step is answered in-process at `now` (the real API uses today's clock)."""
    from offers.segments import list_live_offers
    from pipeline import offer_pipeline
    from pipeline.scheduler import run_round

    with get_connection() as conn:
        opted = [r["user_id"] for r in conn.execute("SELECT user_id FROM user_consent WHERE opted_in = 1")]
        chosen = rng.sample(opted, min(n_users, len(opted)))
        conn.executemany("INSERT OR REPLACE INTO telegram_links (user_id, chat_id, link_token, linked_at, active) "
                         "VALUES (?, ?, ?, ?, 1)", [(u, f"eval{u}", f"evaltoken{u}", now.isoformat()) for u in chosen])
        conn.execute("DELETE FROM audit_log")
    offer_pipeline.fetch_live_offers = lambda user_id, trigger_type=None: list_live_offers(user_id, trigger_type, now=now)
    stats = run_round(now=now, dry_run=True, verbose=False)
    with get_connection() as conn:
        blocks = conn.execute("SELECT reason, COUNT(*) AS n FROM audit_log WHERE action = 'step_eligibility' "
                              "AND decision = 'blocked' GROUP BY reason ORDER BY n DESC").fetchall()
    return {"linked_users": len(chosen), "statuses": dict(stats),
            "eligibility_blocks": {r["reason"]: r["n"] for r in blocks}}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def to_markdown(res: dict) -> str:
    lines = [f"# Offer evaluation (`python -m pipeline.evaluate_offers`)", "",
             f"Snapshot at **{res['now']}** (end of the synthetic data). Triggers found: "
             + ", ".join(f"{k} {v:,}" for k, v in res["triggers_found"].items())
             + f". Evaluated a random sample of **{res['evaluated']}** (festival excluded: no festival window then).", "",
             "## Strategies (same guardrail-safe candidates, planted response model)", "",
             "| Strategy | Offers sent | Redemptions / 1,000 triggers | Incremental / 1,000 | Discount cost / 1,000 (₹) | ₹ per incremental redemption |",
             "|---|---|---|---|---|---|"]
    for s in STRATEGIES:
        r = res["strategies"][s]
        lines.append(f"| {s} | {r['sent']:,} | {r['redeem_per_1000']} | {r['incremental_per_1000']} | "
                     f"{r['cost_per_1000_inr']:,.0f} | {r['cost_per_incremental_inr'] or '–'} |")
    lines += ["", "## By trigger (redemptions / 1,000 · ₹ per incremental redemption)", "",
              "| Trigger | " + " | ".join(STRATEGIES) + " |", "|---|" + "---|" * len(STRATEGIES)]
    triggers = sorted({k.split("|")[0] for k in res["by_trigger"]})
    for t in triggers:
        cells = []
        for s in STRATEGIES:
            r = res["by_trigger"].get(f"{t}|{s}")
            cells.append(f"{r['redeem_per_1000']} · ₹{r['cost_per_incremental_inr'] or '–'}" if r else "–")
        lines.append(f"| {t} | " + " | ".join(cells) + " |")
    g = res["guardrails"]
    lines += ["", "## Guardrails", "",
              f"{g['ranked_in']:,} ranked candidate offers in, **{g['kept']:,} kept** "
              f"({100 * g['kept'] / max(1, g['ranked_in']):.1f}%). Removed, by reason:", "",
              "| Reason | Offers removed |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in g["blocked_reasons"].items()]
    f = res["funnel"]
    lines += ["", "## Pipeline funnel (one dry-run scheduler round)", "",
              f"{f['linked_users']} random opted-in users linked to a fake Telegram chat.", "",
              "| Final status | Runs |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in sorted(f["statuses"].items(), key=lambda kv: -kv[1])]
    if f["eligibility_blocks"]:
        lines += ["", "Eligibility blocks: " + "; ".join(f"{k} ({v})" for k, v in f["eligibility_blocks"].items())]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline evaluation of offer strategies + guardrails.")
    parser.add_argument("--now", default=DEFAULT_NOW)
    parser.add_argument("--max-triggers", type=int, default=1500)
    parser.add_argument("--funnel-users", type=int, default=400)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--only", help="evaluate one trigger type only")
    parser.add_argument("--out", type=Path, default=OUT_MD)
    args = parser.parse_args()
    res = evaluate(datetime.fromisoformat(args.now), args.max_triggers, args.funnel_users, args.seed, args.only)
    args.out.write_text(to_markdown(res), encoding="utf-8")
    args.out.with_suffix(".json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    sys.stdout.reconfigure(errors="replace")  # Windows consoles may not print ₹
    print(to_markdown(res))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
