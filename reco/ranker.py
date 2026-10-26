"""Step 8 — Ranker: LightGBM LambdaRank re-orders the ~200 candidates of each user.

In simple words:
Candidate generators are good at FINDING plausible products, not at ORDERING them.
The ranker looks at many clues ("features") per (user, product) pair — did several
models suggest it? does the user love this category? is it in their price range? is it
trending? did they view it yesterday? — and learns how much each clue matters.

How it learns (no peeking at the future!):
  1. Train the candidate models on data up to day D.
  2. Generate candidates for each user as of day D.
  3. Label them with what the user ACTUALLY did in the next 15 days
     (grade = strongest action: view 1 ... purchase 6, 0 = ignored).
  4. LambdaRank learns to put high-grade products at the top of each user's list.
We do this for two windows (more examples), then evaluate on a later, untouched window.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from common.logging_utils import get_logger
from reco.candidates.merge import SOURCES, merge_candidates
from reco.data import RecoData, Split, UserHistory, all_histories, isin, relevance_labels, shift_date, window

if TYPE_CHECKING:
    from reco.engine import Recommender

log = get_logger(__name__)

FEATURES = [
    *(f"score_{s}" for s in SOURCES), "n_sources",
    "cat_affinity", "item_popularity", "item_trend", "item_age_days",
    "price_gap", "log_price_ratio", "avg_rating", "log_rating_count",
    "days_since_seen", "user_item_weight", "bought_before", "in_cart_or_wishlist", "user_activity",
]
MIN_TRAINING_USERS = 3
MAX_USERS_PER_WINDOW = 1500  # × ~200 candidates = 300k training rows per window


def build_features(cands: pd.DataFrame, history: UserHistory, engine: Recommender) -> pd.DataFrame:
    """One row of numbers per candidate (same order as `cands`). Missing info -> NaN (LightGBM copes)."""
    taste = engine.taste(history)
    meta = engine.items.reindex(cands["product_id"])
    now = pd.to_datetime(history.now or engine.ref_time)
    f = pd.DataFrame(index=cands.index)
    for s in SOURCES:
        f[f"score_{s}"] = cands[f"score_{s}"].values
    f["n_sources"] = cands["n_sources"].values

    f["cat_affinity"] = meta["category_id"].map(taste.cat_affinity).fillna(0.0).values
    f["item_popularity"] = np.log1p(meta["popularity"].fillna(0.0).values)
    f["item_trend"] = np.log1p(meta["trend"].fillna(0.0).values)
    launch = pd.to_datetime(meta["launch_date"], errors="coerce")
    f["item_age_days"] = (now - launch).dt.days.values
    f["price_gap"] = (meta["price_pct"] - taste.price_pos).values if taste.price_pos is not None else np.nan
    if taste.median_price:
        f["log_price_ratio"] = np.log(meta["price"].astype(float) / taste.median_price).values
    else:
        f["log_price_ratio"] = np.nan
    f["avg_rating"] = meta["avg_rating"].astype(float).values
    f["log_rating_count"] = np.log1p(meta["rating_count"].astype(float)).values

    pids = cands["product_id"]
    last_seen = pd.to_datetime(pids.map(history.last_seen), errors="coerce")
    f["days_since_seen"] = (now - last_seen).dt.days.fillna(999).values
    f["user_item_weight"] = np.log1p(pids.map(history.weights).fillna(0.0).astype(float)).values
    f["bought_before"] = isin(pids, history.purchases).astype(int)
    f["in_cart_or_wishlist"] = isin(pids, history.cart | history.wishlist).astype(int)
    f["user_activity"] = np.log1p(taste.total_weight)
    return f[FEATURES].astype(float)


class Ranker:
    """Thin wrapper around lightgbm.LGBMRanker."""

    def fit(self, x: pd.DataFrame, y: np.ndarray, groups: list[int]) -> Ranker:
        import lightgbm as lgb

        self.model = lgb.LGBMRanker(
            objective="lambdarank", n_estimators=150, learning_rate=0.05, num_leaves=15,
            min_child_samples=5, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
            random_state=42, verbose=-1,
        )
        self.model.fit(x[FEATURES], y.astype(int), group=groups)
        self.n_users = len(groups)
        return self

    def predict(self, x: pd.DataFrame) -> np.ndarray:
        return self.model.predict(x[FEATURES])

    def importance(self) -> dict[str, int]:
        """How often each feature was used in the trees (bigger = more important)."""
        pairs = zip(FEATURES, self.model.booster_.feature_importance(importance_type="split"))
        return dict(sorted(((k, int(v)) for k, v in pairs), key=lambda t: -t[1]))


def training_rows(engine: Recommender, histories: dict[str, UserHistory],
                  labels: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray, list[int]]:
    """Candidates + features + grades for every user that has at least one labelled action."""
    xs, ys, groups = [], [], []
    for uid, lab in labels.groupby("user_id"):
        # same "now" as at serving time, so time features mean the same thing in training and use
        hist = engine.at_now(histories.get(uid) or UserHistory(user_id=uid))
        cands = merge_candidates(engine, hist)
        if cands.empty:
            continue
        grades = cands["product_id"].map(dict(zip(lab["product_id"], lab["grade"]))).fillna(0).to_numpy()
        if grades.max() == 0:  # none of the candidates was right -> nothing to learn from this user
            continue
        xs.append(build_features(cands, hist, engine))
        ys.append(grades)
        groups.append(len(cands))
    if not xs:
        return pd.DataFrame(columns=FEATURES), np.array([]), []
    return pd.concat(xs, ignore_index=True), np.concatenate(ys), groups


def ranker_windows(split: Split) -> list[tuple[str, str, str]]:
    """[(fit_until, label_start, label_end)]: the validation window + the same-length window before it."""
    length = (pd.to_datetime(split.valid_end) - pd.to_datetime(split.valid_start)).days + 1
    earlier_start = shift_date(split.valid_start, -length)
    return [
        (shift_date(earlier_start, -1), earlier_start, shift_date(split.valid_start, -1)),
        (split.train_end, split.valid_start, split.valid_end),
    ]


def train_ranker(data: RecoData, split: Split) -> Ranker | None:
    """Train the ranker on rolling windows before the test period. Returns None if data is too small."""
    from reco.engine import Recommender

    if not data.has("ranker"):
        log.info("Ranker skipped: needs a product catalog (data level L2+).")
        return None
    xs, ys, groups = [], [], []
    for fit_until, start, end in ranker_windows(split):
        past = data.until(fit_until)
        if past.events.empty:
            continue
        engine = Recommender.fit(past)
        labels = relevance_labels(window(data.events, start, end))
        users = labels["user_id"].unique()
        if len(users) > MAX_USERS_PER_WINDOW:  # plenty of examples; keeps training to minutes on 1M events
            keep = set(np.random.default_rng(42).choice(users, MAX_USERS_PER_WINDOW, replace=False))
            labels = labels[isin(labels["user_id"], keep)]
        x, y, g = training_rows(engine, all_histories(past, labels["user_id"].unique().tolist()), labels)
        log.info("Ranker window %s..%s: %d users with labels", start, end, len(g))
        xs.append(x), ys.append(y), groups.extend(g)
    if len(groups) < MIN_TRAINING_USERS:
        log.warning("Ranker skipped: only %d training users (need %d). Using blended scores.",
                    len(groups), MIN_TRAINING_USERS)
        return None
    return Ranker().fit(pd.concat(xs, ignore_index=True), np.concatenate(ys), groups)
