"""Step 5 — ALS (Alternating Least Squares) matrix factorization.

In simple words:
Imagine every product has a hidden "taste profile" of ~32 numbers (maybe one number
secretly means "budget vs premium", another "fitness lover"...). Every user has one
too. ALS learns these numbers so that  user · product  is high for products the
user interacted with. Nobody tells it what the numbers mean — it discovers them.

"Implicit feedback": we never see "I dislike this", only actions. So every action is
a *confidence* that the user likes the item: confidence = 1 + 10 × log(1 + weight).

For a user, we don't need a stored profile: we "fold in" — solve their 32 numbers
from their latest history in one small math step. That's how brand-new activity
(and users who appeared after training) still get personal ALS scores.

Fallback: if the `implicit` library can't be installed, we use scikit-learn's
TruncatedSVD, a similar (simpler) factorization.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from common.logging_utils import get_logger
from reco.data import InteractionMatrix

log = get_logger(__name__)

ALPHA = 10.0
REGULARIZATION = 0.05

try:
    from implicit.cpu.als import AlternatingLeastSquares

    HAVE_IMPLICIT = True
except ImportError:  # pragma: no cover - depends on the machine
    HAVE_IMPLICIT = False


def _confidence(values: np.ndarray) -> np.ndarray:
    return 1.0 + ALPHA * np.log1p(values)


class ALSModel:
    name = "als"

    def fit(self, im: InteractionMatrix, factors: int = 32, iterations: int = 20) -> ALSModel:
        self.item_ids = im.item_ids
        self.item_index = im.item_index
        # small catalogs can't support many factors
        factors = max(2, min(factors, len(im.item_ids) // 2, max(len(im.user_ids) - 1, 2)))
        conf = im.matrix.copy().astype(np.float32)
        conf.data = _confidence(conf.data).astype(np.float32)

        if HAVE_IMPLICIT:
            # implicit runs its own threads; letting BLAS also multi-thread makes it slower.
            with threadpool_limits(1, "blas"):
                model = AlternatingLeastSquares(factors=factors, regularization=REGULARIZATION,
                                                iterations=iterations, random_state=42)
                model.fit(conf, show_progress=False)
            self.item_factors = np.asarray(model.item_factors, dtype=np.float32)
            self.backend = "implicit"
        else:
            from sklearn.decomposition import TruncatedSVD

            svd = TruncatedSVD(n_components=factors, random_state=42)
            svd.fit(conf)
            self.item_factors = svd.components_.T.astype(np.float32)
            self.backend = "svd"
        # Pre-compute Yᵀ·Y once: the part of the fold-in equation that's the same for every user.
        self.yty = self.item_factors.T @ self.item_factors
        log.info("ALS trained (%s backend, %d factors, %d items)", self.backend, factors, len(self.item_ids))
        return self

    def user_vector(self, weights: dict[str, float]) -> np.ndarray | None:
        """Fold in: solve this user's factors from their history (ridge regression, ALS-style)."""
        known = [(self.item_index[p], w) for p, w in weights.items() if p in self.item_index]
        if not known:
            return None
        idx = np.array([i for i, _ in known])
        conf = _confidence(np.array([w for _, w in known], dtype=np.float32))
        y = self.item_factors[idx]
        a = self.yty + (y.T * (conf - 1.0)) @ y + REGULARIZATION * np.eye(y.shape[1], dtype=np.float32)
        b = (y.T * conf).sum(axis=1)
        return np.linalg.solve(a, b)

    def candidates(self, weights: dict[str, float], n: int) -> pd.DataFrame:
        """Top-n products by predicted preference. Columns: product_id, score."""
        u = self.user_vector(weights)
        if u is None:
            return pd.DataFrame(columns=["product_id", "score"])
        scores = self.item_factors @ u
        top = np.argsort(-scores)[:n]
        return pd.DataFrame({"product_id": [self.item_ids[j] for j in top], "score": scores[top]})
