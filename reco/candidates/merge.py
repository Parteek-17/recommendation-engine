"""Step 7 — Candidate merge: collect ~200 products per user from every source.

In simple words:
Each generator (item-CF, ALS, content, popularity) suggests its own top list. We put
them all in one table, one row per product, with one score column per source
(0 if that source didn't suggest it). Scores are rescaled to 0..1 per source so they
can be compared. We also remember the "anchor" product (for the reason text) and
which source contributed most ("best_source").

The "blend" column is a simple weighted mix of the sources. It is used when no
trained ranker exists (tiny data) and as an evaluation stage on its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from reco.data import UserHistory

if TYPE_CHECKING:  # avoid a circular import at runtime
    from reco.engine import Recommender

SOURCES = ("item_cf", "als", "content", "popularity")
BLEND_WEIGHTS = {"item_cf": 0.35, "als": 0.35, "content": 0.15, "popularity": 0.15}
CANDIDATES_PER_USER = 200


def _rescale(scores: pd.Series) -> pd.Series:
    """Divide by the best score -> 0..1. Negative scores (ALS "probably not") become 0 = no signal."""
    if scores.empty:
        return scores
    hi = float(scores.max())
    if hi <= 1e-12:
        return pd.Series(0.0, index=scores.index)
    return (scores / hi).clip(lower=0.0)


def source_lists(engine: Recommender, history: UserHistory, n: int) -> dict[str, pd.DataFrame]:
    """Each source's own top-n (columns product_id, score[, anchor])."""
    out: dict[str, pd.DataFrame] = {}
    if engine.item_cf is not None:
        out["item_cf"] = engine.item_cf.candidates(history.weights, n)
    if engine.als is not None:
        out["als"] = engine.als.candidates(history.weights, n)
    if engine.content is not None:
        out["content"] = engine.content.candidates(history.weights, n)
    pop = engine.popularity.candidates(history.profile, n)
    out["popularity"] = pd.DataFrame({"product_id": pop.index, "score": pop.values})
    return out


def merge_candidates(engine: Recommender, history: UserHistory, n: int = CANDIDATES_PER_USER) -> pd.DataFrame:
    """One row per candidate product with score_<source>, anchor_<source>, n_sources, blend, best_source."""
    lists = source_lists(engine, history, n)
    merged: pd.DataFrame | None = None
    for source, df in lists.items():
        if df.empty:
            continue
        part = pd.DataFrame({"product_id": df["product_id"].values,
                             f"score_{source}": _rescale(df["score"].astype(float)).values})
        if "anchor" in df.columns:
            part[f"anchor_{source}"] = df["anchor"].values
        merged = part if merged is None else merged.merge(part, on="product_id", how="outer")

    if merged is None:
        return pd.DataFrame(columns=["product_id", "blend", "best_source"])
    for source in SOURCES:  # missing sources -> zero score, no anchor
        merged[f"score_{source}"] = merged.get(f"score_{source}", pd.Series(0.0, index=merged.index)).fillna(0.0)
    score_cols = [f"score_{s}" for s in SOURCES]
    merged["n_sources"] = (merged[score_cols] > 0).sum(axis=1)
    weighted = pd.DataFrame({s: merged[f"score_{s}"] * BLEND_WEIGHTS[s] for s in SOURCES})
    merged["blend"] = weighted.sum(axis=1)
    merged["best_source"] = weighted.idxmax(axis=1)
    return merged.sort_values("blend", ascending=False).head(n).reset_index(drop=True)
