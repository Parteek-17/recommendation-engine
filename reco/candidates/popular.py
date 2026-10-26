"""Step 3 — Baselines: global popularity and "trending" (recent popularity).

In simple words:
- Popular  = which products got the most (weighted) attention overall.
- Trending = the same, but only the last 14 days, and yesterday counts more than
             two weeks ago ("time decay": an event loses half its value every 7 days).
- Segment popular = popular among users like you (same city tier). Helps brand-new
  users we know nothing about except their signup details. City tier, not age/gender:
  tier tracks price sensitivity (smaller cities buy cheaper), which is what actually
  differs between groups; on the synthetic data age/gender segments were barely
  better than plain trending (cold-start NDCG 0.135 -> 0.143, test window).

Every smarter model must beat these, otherwise it isn't worth its complexity.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from common.schemas import EVENT_WEIGHTS
from reco.data import isin

TRENDING_DAYS = 14
HALF_LIFE_DAYS = 7.0
SEGMENT_SHARE = 0.8  # weight of "popular with people like you" vs. plain trending (tuned on cold users)
SEGMENT_COLUMNS = ["city_tier"]


class PopularityModel:
    name = "popularity"

    def fit(self, events: pd.DataFrame, users: pd.DataFrame, ref_time: str) -> PopularityModel:
        ev = events[events["event_type"].isin(EVENT_WEIGHTS) & events["product_id"].notna()]
        weight = ev["event_type"].map(EVENT_WEIGHTS).astype(float)
        self.global_scores: pd.Series = weight.groupby(ev["product_id"]).sum().sort_values(ascending=False)

        # Trending: last 14 days, with time decay.
        age_days = (pd.to_datetime(ref_time) - pd.to_datetime(ev["timestamp"])).dt.total_seconds() / 86400
        recent = age_days <= TRENDING_DAYS
        decayed = weight[recent] * np.power(0.5, age_days[recent] / HALF_LIFE_DAYS)
        self.trending_scores: pd.Series = decayed.groupby(ev.loc[recent, "product_id"]).sum().sort_values(
            ascending=False)

        # Segment popularity (only when we have a users table with demographics).
        self.segment_scores: dict[tuple, pd.Series] = {}
        if (not users.empty and all(c in users for c in SEGMENT_COLUMNS)
                and users[SEGMENT_COLUMNS].notna().all(axis=1).any()):
            seg = ev.merge(users[["user_id"] + SEGMENT_COLUMNS].dropna(), on="user_id", how="inner")
            seg_w = seg["event_type"].map(EVENT_WEIGHTS).astype(float)
            levels = list(range(len(SEGMENT_COLUMNS)))
            keys = [seg[c].map(_key) for c in SEGMENT_COLUMNS]
            for key, scores in seg_w.groupby(keys + [seg["product_id"]]).sum().groupby(level=levels):
                key = key if isinstance(key, tuple) else (key,)
                self.segment_scores[key] = scores.droplevel(levels).sort_values(ascending=False)
        return self

    def trending(self, n: int, allowed: set[str] | None = None) -> pd.Series:
        """Top-n trending products (back-filled with all-time popular ones if the window is quiet)."""
        scores = self.trending_scores
        if allowed is not None:
            scores = scores[isin(scores.index, allowed)]
        if len(scores) < n:  # quiet period -> top up with all-time popular, scored below the trending ones
            extra = self.global_scores[~isin(self.global_scores.index, set(scores.index))]
            if allowed is not None:
                extra = extra[isin(extra.index, allowed)]
            floor = float(scores.min()) if len(scores) else 1.0
            extra = extra / (float(extra.max()) or 1.0) * floor * 0.99
            scores = pd.concat([scores, extra])
        return scores.head(n)

    def candidates(self, profile: dict, n: int) -> pd.Series:
        """Popular items for this user: mix of trending + popular in their demographic segment."""
        trend = self.trending(n)
        trend = trend / (float(trend.max()) or 1.0)
        key = tuple(_key(profile.get(c)) for c in SEGMENT_COLUMNS)
        seg = self.segment_scores.get(key)
        if seg is None or seg.empty:
            return trend
        seg = seg.head(n) / float(seg.max())
        mixed = trend.mul(1 - SEGMENT_SHARE).add(seg.mul(SEGMENT_SHARE), fill_value=0.0)
        return mixed.sort_values(ascending=False).head(n)


def _key(value) -> str:
    """Segment values as text: city_tier may be 3, 3.0 or "3" depending on where it was read."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return str(value)
