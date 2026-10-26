"""Step 10 — Reasons: a short, human-readable "why" for every recommendation.

The reason comes from WHERE the product came from (its strongest source) plus the
"anchor" product that triggered it:
    item_cf    -> "Because you viewed Redmi Note 13 5G"
    content    -> "Similar to Yoga Mat 6mm you added to cart"
    als        -> "Matches your taste in Fitness Equipment"
    popularity -> "Trending in Sports this week"
    complement -> "Goes well with your Samsung Galaxy M35"
    restock    -> "Time to restock Bru Premium Tea? You bought it 27 days ago"
plus " · Within your usual budget" when the price fits the user's usual level.

Reasons are built only from our own data (titles, categories) — never invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from reco.data import UserHistory

if TYPE_CHECKING:
    from reco.engine import Recommender, Taste

BUDGET_FIT = 0.2  # within ±0.2 of the user's usual price position -> "within your usual budget"


def anchor_of(row: dict) -> str | None:
    """The user's product that triggered this candidate (from item-CF or content), if any."""
    source = row.get("best_source") or row.get("source")
    for key in (f"anchor_{source}", "anchor_item_cf", "anchor_content", "anchor"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _verb(history: UserHistory, product_id: str) -> str:
    if product_id in history.purchases:
        return "bought"
    if product_id in history.cart:
        return "added to cart"
    if product_id in history.wishlist:
        return "wishlisted"
    return "viewed"


def _category_of(engine: Recommender, product_id: str) -> str:
    cat = engine.items["category_id"].get(product_id) if product_id in engine.items.index else None
    return engine.category_name(cat)


def reason(row: dict, engine: Recommender, history: UserHistory, taste: Taste, gift: bool = False) -> str:
    pid = row["product_id"]
    kind = row.get("reason_type") or row.get("source")
    anchor = row.get("anchor")
    anchor = anchor if isinstance(anchor, str) and anchor else None
    cat = _category_of(engine, pid)

    if gift or kind == "gift":
        text = f"Popular gift pick in {cat}" if cat else "Popular gift pick"
    elif kind == "restock" and anchor:
        days = _days_ago(history.purchases.get(anchor), history.now or engine.ref_time)
        text = f"Time to restock {engine.title(anchor)}?" + (f" You bought it {days} days ago" if days else "")
    elif kind == "complement" and anchor:
        text = f"Goes well with your {engine.title(anchor)}"
    elif kind in ("item_cf", "content") and anchor == pid:
        text = f"You {_verb(history, pid)} this recently — still interested?"
    elif kind == "item_cf" and anchor:
        text = f"Because you {_verb(history, anchor)} {engine.title(anchor)}"
    elif kind == "content" and anchor:
        text = f"Similar to {engine.title(anchor)} you {_verb(history, anchor)}"
    elif kind == "als":
        top = taste.top_categories(3)
        item_cat = engine.items["category_id"].get(pid)
        text = f"Matches your taste in {cat}" if cat and item_cat in top else "Picked for your shopping style"
    elif history.is_cold and history.profile.get("age_group"):
        text = "Popular with shoppers like you"
    else:
        trending = float(engine.items["trend"].get(pid, 0) or 0) > 0
        text = (f"Trending in {cat} this week" if trending else f"Popular in {cat}") if cat else "Popular right now"

    if not gift and taste.price_pos is not None and kind not in ("restock",):
        pct = engine.items["price_pct"].get(pid)
        if pd.notna(pct) and abs(float(pct) - taste.price_pos) <= BUDGET_FIT:
            text += " · Within your usual budget"
    return text


def _days_ago(earlier: str | None, now: str) -> int | None:
    if not earlier:
        return None
    try:
        return max(0, (pd.to_datetime(now) - pd.to_datetime(earlier)).days)
    except (ValueError, TypeError):
        return None


def similar_reason(source: str, title: str) -> str:
    if source == "item_cf":
        return f"Shoppers who viewed {title} also viewed this"
    return f"Similar to {title}"


def complement_reason(kind: str, title: str) -> str:
    if kind == "co_buy":
        return f"Frequently bought with {title}"
    return f"Goes well with {title}"


def trending_reason(engine: Recommender, product_id: str, category_id: str | None, trend: float) -> str:
    cat = engine.category_name(category_id) if category_id else _category_of(engine, product_id)
    if trend <= 0:
        return f"Popular in {cat}" if cat else "Popular right now"
    return f"Trending in {cat} this week" if cat else "Trending this week"
