"""Step 4 — Item-item collaborative filtering ("people who liked X also liked Y").

In simple words:
- Two products are SIMILAR if the same users interact with both. We measure it with
  "cosine similarity" on the user×item matrix: 1.0 = exactly the same audience, 0 = none shared.
  A pair that shares only one user can look "perfect" by luck, so we shrink scores of
  pairs with few shared users ("shrinkage").
- To recommend for a user: for every product they touched, look up its most similar
  products and add up the scores. The product that contributed most is the "anchor",
  which powers the reason "Because you viewed <anchor>".
- COMPLEMENTS (phone -> cover) come from purchase behaviour instead: products bought
  (or added to cart) by the same user within 30 days, from DIFFERENT categories.
  Same-category pairs are substitutes (two phones), not complements.
  If a product has no co-buy data yet, we fall back to category level: "people who buy
  Mobiles also buy Mobile Accessories" -> popular items from that category.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd
from scipy import sparse

from reco.data import InteractionMatrix

TOP_K_NEIGHBOURS = 50
SHRINKAGE = 3.0           # a pair needs a few shared users before its similarity counts fully
CO_BUY_WINDOW_DAYS = 30
MIN_CATEGORY_CONFIDENCE = 0.3  # category pairs rarer than this need 2+ shoppers before we believe them
MAX_INTENT_ITEMS_PER_USER = 100


def _top_k_per_row(mat: sparse.csr_matrix, k: int) -> sparse.csr_matrix:
    """Keep only the k biggest values in each row (saves memory, removes weak noise)."""
    mat = mat.tocsr()
    rows, cols, vals = [], [], []
    for i in range(mat.shape[0]):
        start, end = mat.indptr[i], mat.indptr[i + 1]
        data, idx = mat.data[start:end], mat.indices[start:end]
        if len(data) > k:
            keep = np.argpartition(-data, k)[:k]
            data, idx = data[keep], idx[keep]
        rows.extend([i] * len(data))
        cols.extend(idx)
        vals.extend(data)
    return sparse.csr_matrix((vals, (rows, cols)), shape=mat.shape, dtype=np.float32)


class ItemCF:
    name = "item_cf"

    def fit(self, im: InteractionMatrix, intent_events: pd.DataFrame, item_cat: dict[str, str],
            popularity: pd.Series) -> ItemCF:
        """im: user×item weights. intent_events: purchase/add_to_cart rows (user_id, product_id, timestamp)."""
        self.item_ids = im.item_ids
        self.item_index = im.item_index
        self.item_cat = item_cat

        # --- similarity (cosine with shrinkage) ---
        x = im.matrix.copy().astype(np.float32)
        x.data = np.log1p(x.data)  # 50 views shouldn't count 50× more than 1 purchase
        norms = np.sqrt(np.asarray(x.multiply(x).sum(axis=0))).ravel()
        norms[norms == 0] = 1.0
        x_norm = x @ sparse.diags(1.0 / norms)
        cosine = (x_norm.T @ x_norm).tocsr()
        binary = (im.matrix > 0).astype(np.float32)
        shared = (binary.T @ binary).tocsr()
        shared.data = shared.data / (shared.data + SHRINKAGE)
        sim = cosine.multiply(shared).tocsr()
        sim.setdiag(0)
        sim.eliminate_zeros()
        self.sim = _top_k_per_row(sim, TOP_K_NEIGHBOURS)

        self._fit_complements(intent_events, popularity)
        return self

    # ------------------------------------------------------------------ complements
    def _fit_complements(self, intent: pd.DataFrame, popularity: pd.Series) -> None:
        # Count, for every product pair, how many DIFFERENT users bought/carted both (plain integers:
        # a set of user IDs per pair runs out of memory on 1M events).
        pair_users: dict[tuple[str, str], int] = defaultdict(int)
        item_users: dict[str, int] = defaultdict(int)
        intent = intent.assign(ts=pd.to_datetime(intent["timestamp"]))
        for _, g in intent.groupby("user_id"):
            # first time each product was bought/carted; heavy users capped to their latest 100
            g = g.sort_values("ts").drop_duplicates("product_id").tail(MAX_INTENT_ITEMS_PER_USER)
            pids, days = g["product_id"].tolist(), (g["ts"].astype("int64") // 86_400_000_000_000).tolist()
            for pid in pids:
                item_users[pid] += 1
            pairs = set()
            for i in range(len(pids)):
                for j in range(i + 1, len(pids)):
                    if days[j] - days[i] > CO_BUY_WINDOW_DAYS:
                        break
                    if not self._same_category(pids[i], pids[j]):  # same category = substitutes
                        pairs.add((pids[i], pids[j]))
                        pairs.add((pids[j], pids[i]))
            for pair in pairs:
                pair_users[pair] += 1

        # Category level: of everything bought alongside category A, what share is category B?
        # (Mobiles -> Mobile Accessories is a strong, repeated pattern; Mobiles -> Yoga Mats is chance.)
        cat_pairs: dict[tuple[str, str], float] = defaultdict(float)
        for (a, b), users in pair_users.items():
            ca, cb = self.item_cat.get(a), self.item_cat.get(b)
            if ca and cb:
                cat_pairs[(ca, cb)] += users
        cat_totals: dict[str, float] = defaultdict(float)
        for (ca, _), cnt in cat_pairs.items():
            cat_totals[ca] += cnt
        cat_conf = {pair: cnt / cat_totals[pair[0]] for pair, cnt in cat_pairs.items()}
        self.cat_complements: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for (ca, cb), conf in cat_conf.items():
            self.cat_complements[ca].append((cb, conf))
        for ca in self.cat_complements:
            self.cat_complements[ca].sort(key=lambda t: -t[1])

        # Item level: score = shared buyers / sqrt(buyers of a × buyers of b),
        # weighted by how typical the category pair is, so one-off baskets count less.
        self.co_buy: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for (a, b), users in pair_users.items():
            conf = cat_conf.get((self.item_cat.get(a), self.item_cat.get(b)), 0.5)  # unknown category -> neutral
            if users == 1 and conf < MIN_CATEGORY_CONFIDENCE:
                continue  # one shopper + unusual category combination = chance, not a complement
            score = users / np.sqrt(item_users[a] * item_users[b])
            self.co_buy[a].append((b, float(score * conf)))
        for a in self.co_buy:
            self.co_buy[a].sort(key=lambda t: -t[1])

        # Popular items per category (to fill category-level complements).
        self.popular_by_cat: dict[str, list[tuple[str, float]]] = defaultdict(list)
        top = float(popularity.max()) if len(popularity) else 1.0
        for pid, score in popularity.items():
            cat = self.item_cat.get(pid)
            if cat:
                self.popular_by_cat[cat].append((pid, float(score) / top))

    def _same_category(self, a: str, b: str) -> bool:
        ca, cb = self.item_cat.get(a), self.item_cat.get(b)
        return bool(ca) and ca == cb  # unknown category -> can't tell, treat as different

    # ------------------------------------------------------------------ queries
    def similar(self, product_id: str, n: int) -> pd.Series:
        """Most similar products by co-interaction (empty if the product has no interactions)."""
        i = self.item_index.get(product_id)
        if i is None:
            return pd.Series(dtype=float)
        row = self.sim.getrow(i)
        s = pd.Series(row.data, index=[self.item_ids[j] for j in row.indices])
        return s.sort_values(ascending=False).head(n)

    def complements(self, product_id: str, n: int) -> list[tuple[str, float, str]]:
        """[(product_id, score, kind)] kind = 'co_buy' (item level) or 'category' (fallback)."""
        out = [(pid, score, "co_buy") for pid, score in self.co_buy.get(product_id, [])[:n]]
        seen = {product_id, *(p for p, _, _ in out)}
        cat = self.item_cat.get(product_id)
        for comp_cat, cat_score in self.cat_complements.get(cat, []):
            for pid, pop in self.popular_by_cat.get(comp_cat, [])[:n]:
                if len(out) >= n:
                    return out
                if pid not in seen:
                    # category-level guesses always rank below real co-buy evidence
                    out.append((pid, 0.5 * cat_score * pop, "category"))
                    seen.add(pid)
        return out[:n]

    def candidates(self, weights: dict[str, float], n: int) -> pd.DataFrame:
        """Score every product by similarity to what the user touched. Columns: product_id, score, anchor."""
        known = [(self.item_index[p], np.log1p(w)) for p, w in weights.items() if p in self.item_index]
        if not known:
            return pd.DataFrame(columns=["product_id", "score", "anchor"])
        idx = np.array([i for i, _ in known])
        w = np.array([v for _, v in known], dtype=np.float32)
        sub = self.sim[idx]                              # rows = user's items
        scores = np.asarray(sub.T @ w).ravel()
        top = np.argsort(-scores)[:n]
        top = top[scores[top] > 0]
        if len(top) == 0:
            return pd.DataFrame(columns=["product_id", "score", "anchor"])
        contrib = sub[:, top].toarray() * w[:, None]     # who contributed most to each candidate?
        anchors = idx[contrib.argmax(axis=0)]
        return pd.DataFrame({
            "product_id": [self.item_ids[j] for j in top],
            "score": scores[top],
            "anchor": [self.item_ids[a] for a in anchors],
        })
