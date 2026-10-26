"""The Recommender: holds every trained model and runs the multi-stage pipeline.

    candidates (item-CF, ALS, content, popularity)  ->  ranker (LightGBM)  ->  re-ranking rules
    + a human-readable reason for every item

`Recommender.fit(data)` trains all models on a RecoData snapshot. The public functions in
reco/service.py load ONE fitted Recommender (from models/) and pass it fresh user histories.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from common.logging_utils import get_logger
from common.schemas import RecItem
from reco import explain
from reco.candidates.als import ALSModel
from reco.candidates.content import ContentModel
from reco.candidates.item_cf import ItemCF
from reco.candidates.merge import SOURCES, merge_candidates
from reco.candidates.popular import PopularityModel
from reco.data import RecoData, UserHistory, build_matrix, isin, weighted_interactions
from reco.rerank import rerank

log = get_logger(__name__)

STAGES = ("popularity", "item_cf", "als", "content", "merged", "ranker", "full")
LONG_TAIL_HEAD_SHARE = 0.20      # top 20% most popular products = "head"; the rest = long tail
SUBSTITUTE_PRICE_RATIO = 4.0     # same category + same kind AND within 4× price = substitute
MAX_KINDS_PER_CATEGORY = 10      # title-based kinds are trusted only if a category has a few clear ones...
MIN_PRODUCTS_PER_KIND = 5        # ...each shared by several products


@dataclass
class Taste:
    """A user's taste summarised from their history (gift sessions excluded)."""

    cat_affinity: dict[str, float] = field(default_factory=dict)  # category -> share of attention (sums to 1)
    price_pos: float | None = None   # 0 = always picks the cheapest in a category, 1 = the priciest
    median_price: float | None = None
    total_weight: float = 0.0

    @property
    def budget_band(self) -> str:
        if self.price_pos is None:
            return "mid"
        return "low" if self.price_pos < 0.35 else "high" if self.price_pos > 0.65 else "mid"

    def top_categories(self, n: int = 3) -> list[str]:
        return [c for c, _ in sorted(self.cat_affinity.items(), key=lambda t: -t[1])[:n]]


class Recommender:
    """All models + item metadata. Create with Recommender.fit(data)."""

    item_cf: ItemCF | None
    als: ALSModel | None
    content: ContentModel | None
    ranker = None  # reco.ranker.Ranker, attached after training

    # ------------------------------------------------------------------ training
    @classmethod
    def fit(cls, data: RecoData) -> Recommender:
        self = cls()
        self.ref_time = data.ref_time
        self.level = data.level
        self.features = {f: data.has(f) for f in ("content", "ranker", "reranking", "demographics", "post_purchase")}
        self.cat_names = dict(zip(data.categories["category_id"], data.categories["name"]))
        self.cat_parent = {c: p for c, p in zip(data.categories["category_id"], data.categories["parent_id"])
                           if isinstance(p, str) and p}
        self.items = self._build_items(data)
        self.item_set = set(self.items.index)  # fast "is this a known product?" checks

        personal = weighted_interactions(data.events, data.sessions, personal=True)
        matrix = build_matrix(personal, self.items.index.tolist())

        users = data.users if self.features["demographics"] else data.users.iloc[0:0]
        self.popularity = PopularityModel().fit(data.events, users, self.ref_time)
        self._add_popularity_columns()

        enough = matrix.matrix.nnz > 0 and len(matrix.user_ids) >= 2
        item_cat = self.items["category_id"].dropna().to_dict()
        intent = data.events[data.events["event_type"].isin(["purchase", "add_to_cart"])
                             & data.events["product_id"].notna()]
        self.item_cf = ItemCF().fit(matrix, intent, item_cat, self.popularity.global_scores) if enough else None
        self.als = ALSModel().fit(matrix) if enough and len(self.items) >= 4 else None
        catalog_ok = self.features["content"] and self.items["title"].notna().any()
        self.content = ContentModel().fit(self.items.reset_index(), data.categories) if catalog_ok else None
        log.info("Recommender fitted on %d events, %d users, %d items (level %s)",
                 len(data.events), len(matrix.user_ids), len(self.items), self.level)
        return self

    @staticmethod
    def _build_items(data: RecoData) -> pd.DataFrame:
        """Product metadata table. Without a catalog (L1) we still list every product seen in events."""
        cols = ["title", "description", "tags", "category_id", "brand", "price", "stock_qty", "is_consumable",
                "repurchase_days", "age_restricted", "avg_rating", "rating_count", "launch_date"]
        if data.products.empty:
            ids = sorted(data.events["product_id"].dropna().unique())
            items = pd.DataFrame(index=pd.Index(ids, name="product_id"), columns=cols)
        else:
            items = data.products.drop_duplicates("product_id").set_index("product_id")[cols].copy()
            extra = set(data.events["product_id"].dropna()) - set(items.index)
            if extra:  # products in events but missing from catalog
                items = pd.concat([items, pd.DataFrame(index=sorted(extra), columns=cols)])
        items.index.name = "product_id"
        # Price position inside its own category: 0 = cheapest, 1 = most expensive.
        items["price_pct"] = items.groupby("category_id")["price"].rank(pct=True)
        return items

    def _add_popularity_columns(self) -> None:
        pop = self.popularity.global_scores.reindex(self.items.index).fillna(0.0)
        self.items["popularity"] = pop
        self.items["trend"] = self.popularity.trending_scores.reindex(self.items.index).fillna(0.0)
        head_size = max(1, int(round(len(pop) * LONG_TAIL_HEAD_SHARE)))
        head = pop.sort_values(ascending=False).iloc[:head_size]
        self.items["is_longtail"] = ~isin(self.items.index, set(head[head > 0].index))

    # ------------------------------------------------------------------ helpers
    def title(self, product_id: str) -> str:
        t = self.items["title"].get(product_id) if product_id in self.items.index else None
        return str(t) if isinstance(t, str) and t else product_id

    def category_name(self, category_id) -> str:
        return self.cat_names.get(category_id, "") if isinstance(category_id, str) else ""

    def at_now(self, history: UserHistory) -> UserHistory:
        """'Now' for this user = their latest event or the data's latest time, whichever is later
        (an inactive user who comes back must not be stuck in the past for restock/60-day rules)."""
        now = max(history.now or "", self.ref_time)
        return history if history.now == now else replace(history, now=now)

    def taste(self, history: UserHistory) -> Taste:
        if not history.weights:
            return Taste()
        w = pd.Series(history.weights)
        w = w[isin(w.index, self.item_set)]
        if w.empty:
            return Taste()
        meta = self.items.loc[w.index]
        cats = w.groupby(meta["category_id"]).sum()
        cats = cats / cats.sum() if cats.sum() > 0 else cats
        priced = meta["price_pct"].notna()
        price_pos = float(np.average(meta.loc[priced, "price_pct"], weights=w[priced])) if priced.any() else None
        median_price = float(meta["price"].median()) if meta["price"].notna().any() else None
        return Taste(cats.to_dict(), price_pos, median_price, float(w.sum()))

    def kinds(self) -> pd.Series:
        """What JOB each product does, guessed from its title: the last word before any "(".
        "Redmi Flagship 5G Smartphone (12GB/256GB)" -> "smartphone", "Spigen Back Cover (...)" -> "cover".
        A leaf category can hold several kinds (Mobile Accessories = covers, chargers, earbuds...).
        Where titles don't give a few clear kinds (books end in the author's name, exam guides in
        "Class 10"), the whole category counts as one kind. Computed once, then cached."""
        cache = self.__dict__.get("_kinds")
        if cache is None:
            def noun(title) -> str | None:
                words = title.split("(")[0].split() if isinstance(title, str) else []
                return words[-1].lower() if words else None

            kind = self.items["title"].map(noun)
            cat = self.items["category_id"]
            for c, group in kind.groupby(cat):
                sizes = group.value_counts()
                clear = (group.notna().all() and len(sizes) <= MAX_KINDS_PER_CATEGORY
                         and sizes.median() >= MIN_PRODUCTS_PER_KIND
                         and all(w.replace("-", "").isalpha() for w in sizes.index))  # "Class 10": no kind
                if not clear:
                    kind[(cat == c).values] = c
            cache = self.__dict__["_kinds"] = kind.where(kind.notna(), cat)
        return cache

    def substitutes_of(self, product_id: str) -> frozenset[str]:
        """Products you'd buy INSTEAD of this one: same leaf category, same kind (a phone cover is
        not a substitute for a charger) and similar price (within 4×). Computed once, then cached."""
        cache = self.__dict__.setdefault("_substitute_cache_v2", {})
        if product_id not in cache:
            subs: set[str] = set()
            if product_id in self.items.index and isinstance(self.items.at[product_id, "category_id"], str):
                row = self.items.loc[product_id]
                kinds = self.kinds()
                same = self.items[(self.items["category_id"] == row["category_id"])
                                  & (kinds == kinds.get(product_id)).values]
                if pd.notna(row["price"]) and row["price"] > 0:
                    ratio = same["price"] / row["price"]
                    same = same[ratio.isna() | ratio.between(1 / SUBSTITUTE_PRICE_RATIO, SUBSTITUTE_PRICE_RATIO)]
                subs = {p for p in same.index if p != product_id}
            cache[product_id] = frozenset(subs)
        return cache[product_id]

    def is_substitute(self, a: str, b: str) -> bool:
        return a == b or b in self.substitutes_of(a)

    def in_stock(self, product_ids) -> pd.Series:
        stock = self.items["stock_qty"].reindex(product_ids)
        return (stock.isna() | (stock > 0)).set_axis(list(product_ids))

    # ------------------------------------------------------------------ scoring
    def score_candidates(self, history: UserHistory, stage: str = "full",
                         cands: pd.DataFrame | None = None) -> pd.DataFrame:
        """Candidates with a `score` column for the given stage, best first (before re-ranking).

        cands: pre-computed merge_candidates() output (evaluation reuses it across stages).
        """
        cands = merge_candidates(self, history) if cands is None else cands
        if cands.empty:
            return cands.assign(score=[])
        if stage in SOURCES:
            cands = cands.assign(score=cands[f"score_{stage}"])
            # a single model scores only its own suggestions; popularity fills the rest (fair comparison)
            fill = cands["score"] == 0
            cands.loc[fill, "score"] = cands.loc[fill, "score_popularity"] * 1e-3
            cands.loc[~fill, "best_source"] = stage
        elif stage == "merged" or self.ranker is None or history.is_cold:
            # Cold users: the ranker's clues are almost all personal (affinity, price gap, recency),
            # so without history it would only shuffle the trending list. Keep popularity order.
            cands = cands.assign(score=cands["blend"])
        else:
            from reco.ranker import build_features
            cands = cands.assign(score=self.ranker.predict(build_features(cands, history, self)))
        return cands.sort_values("score", ascending=False).reset_index(drop=True)

    def recommend_frame(self, history: UserHistory, k: int = 10, stage: str = "full",
                        context: dict | None = None, disabled_rules: frozenset[str] = frozenset(),
                        cands: pd.DataFrame | None = None) -> pd.DataFrame:
        """Top-k as a DataFrame (product_id, score, source, reason_type, anchor). Used by evaluation too."""
        context = context or {}
        gift = bool(context.get("is_gift_mode"))
        history = self.at_now(history)
        # Gift mode: the recipient's taste is not the user's -> ignore personal history entirely.
        hist = UserHistory(user_id=history.user_id, profile=history.profile, now=history.now) if gift else history
        cands = self.score_candidates(hist, stage, None if gift else cands)
        if stage != "full":
            top = cands.head(k)
            return pd.DataFrame({"product_id": top["product_id"], "score": top["score"],
                                 "source": top["best_source"], "reason_type": top["best_source"],
                                 "anchor": [explain.anchor_of(r) for r in top.to_dict("records")]})
        return rerank(cands, hist, self.taste(hist), self, k, context, is_minor=_is_minor(history),
                      disabled=disabled_rules)

    def recommend(self, history: UserHistory, k: int = 10, context: dict | None = None,
                  stage: str = "full") -> list[RecItem]:
        history = self.at_now(history)
        frame = self.recommend_frame(history, k, stage, context)
        taste = self.taste(history)
        gift = bool((context or {}).get("is_gift_mode"))
        recs = []
        top = float(frame["score"].max()) if len(frame) else 1.0
        for row in frame.to_dict("records"):
            reason = explain.reason(row, self, history, taste, gift=gift)
            score = float(row["score"]) / top if top > 0 else float(row["score"])
            recs.append(RecItem(product_id=row["product_id"], score=round(score, 4), reason=reason,
                                source=str(row["source"])))
        return recs

    # ------------------------------------------------------------------ item-to-item
    def similar(self, product_id: str, k: int = 10) -> list[RecItem]:
        """Substitutes / alternatives: co-viewed (item-CF) + similar text (content)."""
        if product_id not in self.items.index:
            return []
        parts = {}
        if self.item_cf is not None:
            parts["item_cf"] = self.item_cf.similar(product_id, 50)
        if self.content is not None:
            parts["content"] = self.content.similar(product_id, 50)
        frame = pd.DataFrame({s: v / (float(v.max()) or 1.0) for s, v in parts.items() if len(v)}).fillna(0.0)
        if frame.empty:
            return []
        frame["score"] = frame.mean(axis=1)
        same_cat = self.items["category_id"].reindex(frame.index) == self.items.at[product_id, "category_id"]
        frame["score"] += 0.2 * same_cat.astype(float)  # "similar" should mostly mean "same kind of thing"
        frame["source"] = frame[[c for c in parts if c in frame]].idxmax(axis=1)
        frame = frame[self.in_stock(frame.index).values].sort_values("score", ascending=False).head(k)
        top = float(frame["score"].max()) or 1.0
        name = self.title(product_id)
        return [RecItem(product_id=pid, score=round(float(r["score"]) / top, 4), source=r["source"],
                        reason=explain.similar_reason(r["source"], name))
                for pid, r in frame.iterrows()]

    def complements(self, product_id: str, k: int = 10) -> list[RecItem]:
        """Products that go WITH this one (bought together, different category)."""
        if self.item_cf is None or product_id not in self.items.index:
            return []
        found = self.item_cf.complements(product_id, k * 3)
        found = [(p, s, kind) for p, s, kind in found
                 if p in self.items.index and not self.is_substitute(product_id, p)]
        stock = self.in_stock([p for p, _, _ in found])
        found = [t for t in found if stock[t[0]]][:k]
        if not found:
            return []
        top = max(s for _, s, _ in found) or 1.0
        name = self.title(product_id)
        return [RecItem(product_id=p, score=round(s / top, 4), source="item_cf",
                        reason=explain.complement_reason(kind, name)) for p, s, kind in found]

    def trending(self, k: int = 10, category_id: str | None = None) -> list[RecItem]:
        allowed = self.items.index
        if category_id:
            cats = {category_id} | {c for c, p in self.cat_parent.items() if p == category_id}
            allowed = self.items.index[isin(self.items["category_id"], cats)]
        allowed = allowed[self.in_stock(allowed).values]
        scores = self.popularity.trending(k, set(allowed))
        top = float(scores.max()) if len(scores) else 1.0
        return [RecItem(product_id=pid, score=round(float(s) / (top or 1.0), 4), source="popularity",
                        reason=explain.trending_reason(self, pid, category_id, float(self.items.at[pid, "trend"])))
                for pid, s in scores.items()]


def _is_minor(history: UserHistory) -> bool:
    try:
        return int(float(history.profile.get("is_minor", 0) or 0)) == 1
    except (TypeError, ValueError):
        return False
