"""Step 2 — Product relations (GROUND TRUTH, evaluation only).

    relations = build_relations(products, rng)

complement: goes well WITH the product (phone -> cover/charger, laptop -> bag/mouse).
            From COMPLEMENT_RULES in templates.py; 1-5 per product where a rule exists.
substitute: could be bought INSTEAD of it: same sub-category, same kind, price within ±30%.

The simulation uses these relations to plant post-purchase behaviour, and the evaluation
later checks whether our models rediscovered them. Models must never read this file.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from data_gen import config as C
from data_gen.templates import COMPLEMENT_RULES


def _kinds(products: pd.DataFrame) -> pd.Series:
    """Product kind; external catalogs have no kind, so the category stands in for it."""
    return products["_kind"] if "_kind" in products.columns else products["category_id"]


def build_substitutes(products: pd.DataFrame) -> pd.DataFrame:
    rows: list[tuple[str, str]] = []
    prices = products["price"].astype(float)
    for _, group in products.assign(_k=_kinds(products), _p=prices).groupby(["category_id", "_k"], sort=True):
        ids = group["product_id"].to_numpy()
        p = group["_p"].to_numpy()
        # close[i, j] = True when j's price is within ±30% of i's price.
        close = np.abs(p[None, :] - p[:, None]) <= C.SUBSTITUTE_PRICE_BAND * p[:, None]
        np.fill_diagonal(close, False)
        for i, j in zip(*np.nonzero(close)):
            rows.append((ids[i], ids[j]))
    return pd.DataFrame(rows, columns=["product_id", "related_product_id"]).assign(relation="substitute")


def build_complements(products: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    kinds = _kinds(products).to_numpy()
    pop = products["_pop"].to_numpy() if "_pop" in products.columns else np.ones(len(products))
    ids = products["product_id"].to_numpy()
    by_kind: dict[str, np.ndarray] = {k: np.flatnonzero(kinds == k) for k in sorted(set(kinds))}

    rows: list[tuple[str, str]] = []
    for i in range(len(products)):
        targets = [t for t in COMPLEMENT_RULES.get(kinds[i], []) if t in by_kind]
        picked: list[int] = []
        for target in targets:
            pool = by_kind[target]
            n = min(len(pool), int(rng.integers(C.COMPLEMENTS_PER_KIND[0], C.COMPLEMENTS_PER_KIND[1] + 1)))
            # Popular accessories are more likely to be "frequently bought together".
            w = pop[pool] / pop[pool].sum()
            picked.extend(rng.choice(pool, size=n, replace=False, p=w).tolist())
        for j in picked[: C.COMPLEMENTS_PER_PRODUCT[1]]:
            rows.append((ids[i], ids[j]))
    return pd.DataFrame(rows, columns=["product_id", "related_product_id"]).assign(relation="complement")


def build_relations(products: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    rel = pd.concat([build_complements(products, rng), build_substitutes(products)], ignore_index=True)
    return rel.sort_values(["product_id", "relation", "related_product_id"], kind="stable").reset_index(drop=True)
