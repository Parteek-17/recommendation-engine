"""Offer matching: score each live offer by how relevant it is to ONE user.

The Offer Engine (Phase 3) creates offers; we only decide which fits this user best.
Score = sum of simple, explainable parts (the biggest part becomes the reason):
  - product match : the offer's product is in their cart / wishlist / recently viewed,
                    a bundle completes something they bought, or a buy-again offer
                    is for a consumable they buy regularly
  - category match: the offer's category is one the user loves
  - value         : price-sensitive users (they pick cheap options) value discounts more
  - budget fit    : a ₹999 minimum cart for someone whose orders are ~₹300 is a bad fit
  - segment       : 'new_users' / 'tier3' offers only fit those users
Prices, codes and values are never changed here — we only rank.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from common.schemas import Offer, RankedOffer, UserContext
from reco.data import UserHistory

if TYPE_CHECKING:
    from reco.engine import Recommender

RECENT_VIEW_DAYS = 14
NEW_USER_DAYS = 30


def _get(offer, name):
    """Offers may arrive as Offer objects or plain dicts."""
    value = offer.get(name) if isinstance(offer, dict) else getattr(offer, name, None)
    if hasattr(value, "value"):  # enum -> plain text
        value = value.value
    return None if value is None or (isinstance(value, float) and pd.isna(value)) or value == "" else value


def build_user_context(engine: Recommender, history: UserHistory) -> UserContext:
    taste = engine.taste(history)
    return UserContext(user_id=history.user_id, budget_band=taste.budget_band,
                       top_categories=taste.top_categories(3), trigger_type=None, is_gift_mode=False)


def _category_affinity(engine: Recommender, cat_affinity: dict[str, float], category_id: str) -> float:
    """Affinity for a category, counting its children too (offer on 'Electronics' fits a Mobiles fan)."""
    return sum(share for cat, share in cat_affinity.items()
               if cat == category_id or engine.cat_parent.get(cat) == category_id)


def _typical_spend(engine: Recommender, history: UserHistory) -> float | None:
    prices = engine.items["price"].reindex(list(history.purchases) or list(history.weights)).dropna()
    return float(prices.median()) if len(prices) else None


def _days(earlier: str | None, later: str) -> float:
    if not earlier:
        return float("inf")
    try:
        return (pd.to_datetime(later) - pd.to_datetime(earlier)).total_seconds() / 86400
    except (ValueError, TypeError):
        return float("inf")


def rank_offers(engine: Recommender, history: UserHistory, offers: list[Offer | dict]) -> list[RankedOffer]:
    history = engine.at_now(history)
    taste = engine.taste(history)
    now = history.now or engine.ref_time
    spend = _typical_spend(engine, history)
    # price_pos 0 = always cheapest option -> very price sensitive
    sensitivity = 1.0 - taste.price_pos if taste.price_pos is not None else 0.5
    recent_views = {p for p, ts in history.last_seen.items() if _days(ts, now) <= RECENT_VIEW_DAYS}
    ranked = []
    for offer in offers:
        parts: list[tuple[float, str]] = []
        product, bundle = _get(offer, "product_id"), _get(offer, "bundle_product_id")
        category, kind = _get(offer, "category_id"), _get(offer, "offer_type")

        # --- product match
        if product:
            title = engine.title(product)
            if product in history.cart or (bundle and bundle in history.cart):
                parts.append((0.6, f"For the {title} in your cart"))
            elif kind == "buy_again" and product in history.purchases:
                parts.append((0.6, f"Save when you restock {title}"))
            elif bundle and product in history.purchases and bundle not in history.purchases:
                parts.append((0.55, f"Completes your {title}"))
            elif product in history.wishlist:
                parts.append((0.5, f"For {title} on your wishlist"))
            elif product in recent_views or (bundle and bundle in recent_views):
                parts.append((0.3, f"For {title} you viewed recently"))
        # --- category match
        if category:
            aff = _category_affinity(engine, taste.cat_affinity, category)
            if aff > 0:
                parts.append((0.5 * min(1.0, aff * 2), f"Matches your interest in {engine.category_name(category)}"))
        elif not product:
            parts.append((0.15, "Works on everything in the store"))
        # --- value for price-sensitive users
        value = float(_get(offer, "value") or 0)
        value_norm = {"pct_off": min(value / 40, 1.0), "flat_off": min(value / (spend or 1000), 1.0),
                      "free_delivery": 0.3, "points": min(value / 500, 1.0)}.get(kind, min(value / 40, 1.0))
        parts.append((0.2 * sensitivity * value_norm, "A good deal for you"))

        score = 0.05 + sum(s for s, _ in parts)
        # --- penalties
        min_cart = float(_get(offer, "min_cart_value") or 0)
        if spend and min_cart > 2 * spend:
            score *= 0.5
        score *= _segment_fit(_get(offer, "segment"), history, now)
        best = max(parts, key=lambda t: t[0])[1] if parts else "General offer"
        ranked.append(RankedOffer(offer_id=str(_get(offer, "offer_id")), score=round(score, 4), reason=best))
    return sorted(ranked, key=lambda r: r.score, reverse=True)


def _segment_fit(segment: str | None, history: UserHistory, now: str) -> float:
    if not segment or segment == "all":
        return 1.0
    profile = history.profile
    if segment == "new_users":
        signup = profile.get("signup_date")
        return 1.0 if signup is None or _days(str(signup), now) <= NEW_USER_DAYS else 0.3
    if segment.startswith("tier") and profile.get("city_tier") is not None:
        try:
            return 1.0 if int(float(profile["city_tier"])) == int(segment[4:]) else 0.3
        except ValueError:
            return 1.0
    return 1.0  # unknown segment: eligibility is the Offer Engine's job, not ours
