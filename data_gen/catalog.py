"""Step 1 — Catalog: the category tree and the products.

    categories, products = build_catalog(n_products=500, rng=rngs["catalog"])

Products get realistic titles from templates (brand + type + attributes), ₹ prices inside
category ranges, mrp >= price, margins, ratings, stock, tags. ~10% launch late (cold-start
items), consumables get `repurchase_days`, a few are age-restricted.

Besides the contract columns, products carry a few INTERNAL columns (dropped when saving):
    _kind     what job the product does ("smartphone") -> substitutes / complements
    _pop      long-tail popularity weight (Zipf)       -> how often it gets viewed
    _quality  hidden quality (1-5)                      -> drives ratings and reviews
    _query    what shoppers type to find it             -> search events
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from data_gen import config as C
from data_gen.templates import PARENTS, SUBCATEGORIES, ProductType
from data_gen.utils import WINDOW_END, WINDOW_START, nice_price, to_date

SELLING_POINTS = [
    "Rated highly for build quality", "Great value for money", "Trusted by thousands of shoppers",
    "Perfect for everyday use", "Easy to use and maintain", "Comes in premium packaging",
    "Popular gifting choice", "Made with durable materials",
]


def build_categories() -> pd.DataFrame:
    """8 parent categories + 25 sub-categories (parent_id empty for parents)."""
    rows = [{"category_id": cid, "name": name, "parent_id": ""} for cid, name in PARENTS.items()]
    rows += [{"category_id": sid, "name": s["name"], "parent_id": s["parent"]}
             for sid, s in sorted(SUBCATEGORIES.items())]
    return pd.DataFrame(rows)


def allocate_counts(n_products: int) -> dict[str, int]:
    """How many products each sub-category gets (popular categories get more, every one >= 3)."""
    subs = sorted(SUBCATEGORIES)
    w = np.array([np.sqrt(SUBCATEGORIES[s]["weight"]) for s in subs])
    counts = np.maximum(3, np.floor(n_products * w / w.sum())).astype(int)
    # Hand out the leftovers to the biggest categories so the total is exactly n_products.
    order = np.argsort(-w, kind="stable")
    i = 0
    while counts.sum() < n_products:
        counts[order[i % len(order)]] += 1
        i += 1
    while counts.sum() > n_products:  # only for very small sizes, where the minimum of 3 overshoots
        counts[int(np.argmax(counts))] -= 1
    return dict(zip(subs, counts.tolist()))


def _make_title(sub_id: str, ptype: ProductType, brand: str, attrs: list[str]) -> str:
    if SUBCATEGORIES[sub_id]["parent"] == "C007":   # books: "Ikigai (Paperback, English)"
        return f"{ptype.name} ({', '.join(attrs)})"
    return f"{brand} {ptype.name} ({', '.join(attrs)})"


def _tags(*parts: str) -> str:
    """Lower-case, de-duplicated, pipe-separated tags (pipes inside words are removed)."""
    seen: list[str] = []
    for part in parts:
        tag = part.lower().replace("|", " ").strip()
        if tag and tag not in seen:
            seen.append(tag)
    return "|".join(seen)


def _launch_date(rng: np.random.Generator) -> str:
    """10% launched in the last 6 weeks (cold-start items); the rest before the window."""
    if rng.random() < C.LATE_LAUNCH_RATE:
        first = WINDOW_END - timedelta(weeks=C.LATE_LAUNCH_WEEKS) + timedelta(days=1)
        return (first + timedelta(days=int(rng.integers(0, (WINDOW_END - first).days + 1)))).isoformat()
    oldest = to_date(C.OLDEST_LAUNCH)
    return (oldest + timedelta(days=int(rng.integers(0, (WINDOW_START - oldest).days)))).isoformat()


def build_products(n_products: int, rng: np.random.Generator) -> pd.DataFrame:
    rows: list[dict] = []
    for sub_id, count in allocate_counts(n_products).items():
        sub = SUBCATEGORIES[sub_id]
        parent_name = PARENTS[sub["parent"]]
        p_lo, p_hi = C.PARENT_PRICE_RANGES[parent_name]
        seen_titles: set[str] = set()
        for k in range(count):
            # Try a few random combinations until the title is new.
            for _attempt in range(40):
                ptype: ProductType = sub["types"][rng.integers(len(sub["types"]))]
                brands = ptype.brands or sub["brands"]
                brand = brands[rng.integers(len(brands))]
                attrs = [opts[rng.integers(len(opts))] for opts in ptype.attrs]
                title = _make_title(sub_id, ptype, brand, attrs)
                if title not in seen_titles:
                    break
            else:  # every combination taken: make it a distinct variant
                title = f"{title} - Variant {k + 1}"
            seen_titles.add(title)

            lo, hi = max(ptype.lo, p_lo), min(ptype.hi, p_hi)
            price = min(p_hi, max(p_lo, nice_price(np.exp(rng.uniform(np.log(lo), np.log(hi))))))
            mrp = max(price, nice_price(price * (1 + rng.uniform(0, C.MRP_MARKUP_MAX))))
            quality = float(np.clip(rng.normal(C.RATING_MEAN, C.RATING_SD), 1, 5))
            points = rng.choice(SELLING_POINTS, size=2, replace=False)
            rows.append({
                "title": title,
                "description": (f"{title}. {points[0]}. {points[1]}. A popular pick in {sub['name']}. "
                                f"Genuine {brand} product with brand warranty and easy returns."),
                "category_id": sub_id,
                "brand": brand,
                "price": price,
                "mrp": mrp,
                "margin_pct": round(float(rng.uniform(*C.PARENT_MARGINS[parent_name])), 1),
                "_quality": quality,
                "stock_qty": 0 if rng.random() < C.OUT_OF_STOCK_RATE else int(rng.integers(*C.STOCK_RANGE)),
                "launch_date": _launch_date(rng),
                "tags": _tags(sub["name"], brand, ptype.kind.replace("_", " "), *attrs),
                "is_consumable": int(ptype.rep is not None),
                "repurchase_days": int(rng.integers(ptype.rep[0], ptype.rep[1] + 1)) if ptype.rep else None,
                "age_restricted": int(ptype.adult),
                "_kind": ptype.kind,
                "_query": ptype.query or ptype.name.lower(),
            })

    products = pd.DataFrame(rows)
    # Shuffle so product ids don't give away the category, then number them P00001, P00002, ...
    products = products.iloc[rng.permutation(len(products))].reset_index(drop=True)
    products.insert(0, "product_id", [f"P{i + 1:05d}" for i in range(len(products))])

    # Long-tail popularity: a random order of products, weight 1 / rank^1.1 (Zipf).
    rank = rng.permutation(len(products))
    products["_pop"] = 1.0 / (rank + 1) ** C.ZIPF_EXPONENT
    late = products["launch_date"] > C.WINDOW_START
    # Ratings: hidden quality + a little noise; bestsellers have thousands, new launches almost none.
    products["avg_rating"] = np.round(np.clip(products["_quality"] + rng.normal(0, 0.1, len(products)), 1, 5), 1)
    counts = C.RATING_COUNT_TOP / (rank + 1) ** 0.9 * rng.lognormal(0, 0.5, len(products))
    counts = np.where(late, counts * 0.01, counts) + rng.integers(0, 5, len(products))
    products["rating_count"] = counts.astype(int)
    products["repurchase_days"] = products["repurchase_days"].astype("Int64")
    return products


def build_catalog(n_products: int, rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (categories, products)."""
    return build_categories(), build_products(n_products, rng)
