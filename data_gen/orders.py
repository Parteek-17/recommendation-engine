"""Step 5 — Orders, order_items and reviews, derived from the simulated purchases.

    orders, order_items = build_orders(purchases)
    reviews = build_reviews(orders, order_items, products, rng)

One order per session that had purchases (order_date = last purchase in that session).
price_paid already includes any redeemed offer. ~5% of bought items get a review; the
star rating follows the product's hidden quality plus noise.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from data_gen import config as C
from data_gen.templates import REVIEW_TEXTS
from data_gen.utils import WINDOW_END


def build_orders(purchases: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if purchases.empty:
        return (pd.DataFrame(columns=["order_id", "user_id", "order_date", "total_amount"]),
                pd.DataFrame(columns=["order_id", "product_id", "quantity", "price_paid"]))
    # The same product bought twice in one session = one line with a bigger quantity.
    items = (purchases.groupby(["session_id", "product_id", "price_paid"], sort=False)
             .agg(user_id=("user_id", "first"), quantity=("quantity", "sum"), timestamp=("timestamp", "max"))
             .reset_index())
    orders = (items.assign(line=items["quantity"] * items["price_paid"])
              .groupby("session_id", sort=False)
              .agg(user_id=("user_id", "first"), order_date=("timestamp", "max"), total_amount=("line", "sum"))
              .reset_index()
              .sort_values(["order_date", "session_id"], kind="stable")
              .reset_index(drop=True))
    orders["order_id"] = [f"O{i + 1:07d}" for i in range(len(orders))]
    orders["total_amount"] = orders["total_amount"].round(2)
    items = items.merge(orders[["session_id", "order_id", "order_date"]], on="session_id")
    items = items.sort_values(["order_id", "product_id"], kind="stable").reset_index(drop=True)
    order_items = items[["order_id", "product_id", "quantity", "price_paid", "user_id", "order_date"]]
    return orders[["order_id", "user_id", "order_date", "total_amount"]], order_items


def build_reviews(order_items: pd.DataFrame, products: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """order_items must still carry user_id and order_date (build_orders keeps them)."""
    cols = ["review_id", "user_id", "product_id", "rating", "review_text", "review_date"]
    if order_items.empty:
        return pd.DataFrame(columns=cols)
    picked = order_items[rng.random(len(order_items)) < C.REVIEW_RATE].reset_index(drop=True)
    info = products.set_index("product_id")
    quality = (info["_quality"] if "_quality" in info.columns else pd.to_numeric(info["avg_rating"], errors="coerce")
               ).fillna(4.0)
    kind = info["_kind"] if "_kind" in info.columns else pd.Series("product", index=info.index)
    last = datetime.combine(WINDOW_END, datetime.max.time()).replace(microsecond=0)

    rows = []
    for user, product, ordered in zip(picked["user_id"], picked["product_id"], picked["order_date"]):
        rating = int(np.clip(round(quality[product] + rng.normal(0, C.REVIEW_NOISE_SD)), 1, 5))
        when = datetime.fromisoformat(ordered) + timedelta(days=int(rng.integers(*C.REVIEW_DELAY_DAYS)),
                                                           hours=int(rng.integers(0, 12)))
        if when > last:
            continue   # the review would be written after our data window ends
        texts = REVIEW_TEXTS[rating]
        word = str(kind[product]).replace("_", " ")
        rows.append({"user_id": user, "product_id": product, "rating": rating,
                     "review_text": texts[rng.integers(len(texts))].format(kind=word),
                     "review_date": when.isoformat(timespec="seconds")})
    reviews = pd.DataFrame(rows, columns=cols[1:])
    reviews = reviews.sort_values(["review_date", "user_id"], kind="stable").reset_index(drop=True)
    reviews.insert(0, "review_id", [f"R{i + 1:06d}" for i in range(len(reviews))])
    return reviews
