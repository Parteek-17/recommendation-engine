"""Step 6 — Content-based recommendations with TF-IDF (works without ANY interaction data).

In simple words:
- Turn each product's text (title + description + tags + brand + category) into a list
  of word scores. TF-IDF = "how often a word appears in THIS product" × "how rare the
  word is overall". So "galaxy" or "yoga" matter a lot; "genuine product" (in every
  description) counts for almost nothing.
- Two products are similar if their word scores point the same way (cosine similarity).
- A user's "taste text" = the weighted average of the products they interacted with.

Why we need it: a product launched yesterday has no clicks, so CF/ALS can't see it —
but it has a title. That's our cold-start answer for NEW PRODUCTS.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

MAX_PROFILE_ITEMS = 30  # use the user's 30 strongest items for their taste profile


def _product_text(products: pd.DataFrame, cat_names: dict[str, str]) -> pd.Series:
    def col(name: str) -> pd.Series:
        return products[name].fillna("").astype(str)

    category = products["category_id"].map(cat_names).fillna("")
    # Title and brand are repeated: they say more about the product than the long description.
    return (col("title") + " " + col("title") + " " + col("brand") + " " + col("brand") + " "
            + category + " " + col("tags").str.replace("|", " ", regex=False) + " " + col("description"))


class ContentModel:
    name = "content"

    def fit(self, products: pd.DataFrame, categories: pd.DataFrame) -> ContentModel:
        cat_names = dict(zip(categories["category_id"], categories["name"])) if not categories.empty else {}
        self.item_ids = products["product_id"].tolist()
        self.item_index = {p: i for i, p in enumerate(self.item_ids)}
        vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True,
                                     max_df=0.5 if len(products) > 20 else 1.0, max_features=50_000)
        self.vectors = normalize(vectorizer.fit_transform(_product_text(products, cat_names))).tocsr()
        return self

    def similar(self, product_id: str, n: int) -> pd.Series:
        i = self.item_index.get(product_id)
        if i is None:
            return pd.Series(dtype=float)
        scores = np.asarray((self.vectors @ self.vectors[i].T).todense()).ravel()
        scores[i] = -1  # not itself
        top = np.argsort(-scores)[:n]
        top = top[scores[top] > 0]
        return pd.Series(scores[top], index=[self.item_ids[j] for j in top])

    def similarity(self, a: str, b: str) -> float:
        ia, ib = self.item_index.get(a), self.item_index.get(b)
        if ia is None or ib is None:
            return 0.0
        return float(self.vectors[ia].multiply(self.vectors[ib]).sum())

    def candidates(self, weights: dict[str, float], n: int) -> pd.DataFrame:
        """Products closest to the user's taste profile. Columns: product_id, score, anchor."""
        strongest = sorted(((w, p) for p, w in weights.items() if p in self.item_index), reverse=True)
        strongest = strongest[:MAX_PROFILE_ITEMS]
        if not strongest:
            return pd.DataFrame(columns=["product_id", "score", "anchor"])
        idx = np.array([self.item_index[p] for _, p in strongest])
        w = np.log1p(np.array([w for w, _ in strongest]))
        user_items = self.vectors[idx]
        profile = normalize(user_items.T @ w.reshape(-1, 1), axis=0)  # weighted average direction
        scores = np.asarray((self.vectors @ profile)).ravel()
        top = np.argsort(-scores)[:n]
        top = top[scores[top] > 0]
        if len(top) == 0:
            return pd.DataFrame(columns=["product_id", "score", "anchor"])
        # anchor = the user's item most similar to each candidate
        anchor_sim = (user_items @ self.vectors[top].T).toarray()
        anchors = idx[anchor_sim.argmax(axis=0)]
        return pd.DataFrame({
            "product_id": [self.item_ids[j] for j in top],
            "score": scores[top],
            "anchor": [self.item_ids[a] for a in anchors],
        })
