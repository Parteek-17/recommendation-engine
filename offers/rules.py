"""Business rules for offers: discount sizing, money value, margin floor, segments, overlap.

These are small *pure* functions (no database access), shared by the generator, the
guardrails and the analysis. Keeping them in one place means "how big may a discount
be?" has exactly one answer in the whole project.

Limits that a store owner may want to change live in `.env` (MAX_DISCOUNT_PCT,
APPROVAL_THRESHOLD_INR, DAILY_DISCOUNT_BUDGET_INR). The constants below are design
choices of the offer engine itself.
"""

from __future__ import annotations

import math
import re
import secrets

from common.config import settings
from common.schemas import GuardrailResult, Offer, OfferType, TriggerType

# ---------------------------------------------------------------------------
# Constants (design choices — documented in offers/README.md)
# ---------------------------------------------------------------------------

# After any discount we still keep at least this much margin (percentage points of price).
# Example: a phone with margin_pct 5.3 can get at most 5.3 - 2 = 3.3% off.
MIN_MARGIN_AFTER_DISCOUNT_PCT = 2.0

# A discount smaller than this is not worth a message (cart_abandon falls back to free delivery).
MIN_USEFUL_PCT = 3

# Rough ₹ cost of offers that have no price-based value.
DELIVERY_COST_INR = 49
POINT_VALUE_INR = 0.5
# Used when an offer applies to the whole cart and has no min_cart_value.
DEFAULT_ORDER_VALUE_INR = 1000

# Starting discount (%) per trigger, before adjusting for the user's price sensitivity.
BASE_DISCOUNT_PCT: dict[str, float] = {
    TriggerType.CART_ABANDON: 10,
    TriggerType.PRICE_DROP: 8,
    TriggerType.REPURCHASE_DUE: 5,
    TriggerType.POST_PURCHASE: 10,
    TriggerType.WIN_BACK: 15,
    TriggerType.FESTIVAL: 20,
}

# Price-sensitive shoppers (low budget band) get bigger discounts; premium shoppers smaller
# ones, because they often buy anyway (see offers/analysis.py).
SENSITIVITY_MULTIPLIER: dict[str, float] = {"low": 1.5, "mid": 1.0, "high": 0.7}

# How long each kind of offer stays valid.
VALID_DAYS: dict[str, int] = {
    TriggerType.CART_ABANDON: 2,
    TriggerType.PRICE_DROP: 3,
    TriggerType.REPURCHASE_DUE: 7,
    TriggerType.POST_PURCHASE: 7,
    TriggerType.WIN_BACK: 14,
    TriggerType.FESTIVAL: 5,
}

# First part of generated coupon codes, e.g. CART10-7KQ2.
CODE_PREFIX: dict[str, str] = {
    TriggerType.CART_ABANDON: "CART",
    TriggerType.PRICE_DROP: "DROP",
    TriggerType.REPURCHASE_DUE: "AGAIN",
    TriggerType.POST_PURCHASE: "PAIR",
    TriggerType.WIN_BACK: "BACK",
    TriggerType.FESTIVAL: "FEST",
}
# No 0/O or 1/I, so codes are easy to read aloud and type.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# Offer types whose `value` is a percentage of the price.
PCT_TYPES = (OfferType.PCT_OFF, OfferType.BUNDLE, OfferType.BUY_AGAIN)

# ---------------------------------------------------------------------------
# Segments
# ---------------------------------------------------------------------------

# Allowed segment names. Only behaviour / location / account-age based — never personal traits.
ALLOWED_SEGMENTS = {"all", "new_users", "returning", "tier1", "tier2", "tier3", "price_sensitive", "premium"}
# Personal offers (made for one user by the generator) use the segment "user:U00004".
USER_SEGMENT_PREFIX = "user:"
# Words that show a segment is built on a sensitive attribute. Such offers are always blocked.
SENSITIVE_SEGMENT_WORDS = (
    "gender", "male", "female", "men", "women", "boy", "girl",
    "religion", "religious", "hindu", "muslim", "christian", "sikh", "jain", "buddhist",
    "caste", "ethnic", "race", "pregnan", "health", "disab", "sexual", "lgbt",
)
NEW_USER_DAYS = 30


def check_segment(segment: str | None) -> GuardrailResult:
    """Is this segment name allowed at all? (Not: does this user belong to it.)"""
    if not segment:
        return GuardrailResult(allowed=True, reason="no segment = everyone")
    seg = segment.strip().lower()
    words = re.split(r"[^a-z0-9]+", seg)
    if any(w.startswith(bad) for w in words for bad in SENSITIVE_SEGMENT_WORDS):
        return GuardrailResult(allowed=False, reason=f"segment '{segment}' uses a sensitive attribute")
    if seg in ALLOWED_SEGMENTS or (seg.startswith(USER_SEGMENT_PREFIX) and len(seg) > len(USER_SEGMENT_PREFIX)):
        return GuardrailResult(allowed=True, reason="ok")
    return GuardrailResult(allowed=False, reason=f"unknown segment '{segment}'")


def segment_matches(segment: str | None, user_id: str, user_segments: set[str]) -> bool:
    """Does `user_id` belong to `segment`? `user_segments` comes from segments.user_segments()."""
    if not segment or segment.lower() == "all":
        return True
    if segment.lower().startswith(USER_SEGMENT_PREFIX):
        return segment[len(USER_SEGMENT_PREFIX):] == user_id
    return segment.lower() in user_segments


# ---------------------------------------------------------------------------
# Discount sizing + margin floor
# ---------------------------------------------------------------------------


def margin_cap_pct(margin_pct: float | None) -> float:
    """Biggest discount (%) a product with this margin can take without dropping below the floor."""
    if margin_pct is None or (isinstance(margin_pct, float) and math.isnan(margin_pct)):
        return 0.0  # unknown margin -> no discount (safe default)
    return max(0.0, min(float(settings.MAX_DISCOUNT_PCT), float(margin_pct) - MIN_MARGIN_AFTER_DISCOUNT_PCT))


def size_discount_pct(trigger_type: str, budget_band: str | None, cap_pct: float) -> int:
    """Discount for this trigger, bigger for price-sensitive users, never above `cap_pct` or MAX_DISCOUNT_PCT.

    Rounded DOWN to a whole number so rounding can never push us past the margin floor.
    """
    pct = BASE_DISCOUNT_PCT[trigger_type] * SENSITIVITY_MULTIPLIER.get(budget_band or "mid", 1.0)
    pct = min(pct, float(settings.MAX_DISCOUNT_PCT), cap_pct)
    return max(0, math.floor(pct))


def make_code(trigger_type: str, value: float, taken: set[str]) -> str:
    """Unique, readable coupon code like CART10-7KQ2 (adds itself to `taken`)."""
    prefix = CODE_PREFIX.get(trigger_type, "OFFER")
    hint = f"{value:g}" if value else ""
    while True:
        code = f"{prefix}{hint}-" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))
        if code not in taken:
            taken.add(code)
            return code


# ---------------------------------------------------------------------------
# Money value of an offer (used for approval threshold, daily budget, analysis)
# ---------------------------------------------------------------------------


def reference_price(offer: Offer, catalog) -> float:
    """The price the discount is taken from: the product, the category's typical price, or the cart."""
    if offer.offer_type == OfferType.BUNDLE and offer.bundle_product_id:
        price = catalog.price(offer.bundle_product_id)  # bundle discount is on the add-on item
        if price:
            return price
    if offer.product_id:
        price = catalog.price(offer.product_id)
        if price:
            return price
    if offer.category_id:
        price = catalog.median_price(offer.category_id)
        if price:
            return price
    return max(float(offer.min_cart_value or 0), DEFAULT_ORDER_VALUE_INR)


def offer_value_inr(offer: Offer, catalog) -> float:
    """Roughly how many ₹ this offer gives away per use."""
    if offer.offer_type in PCT_TYPES:
        return round(reference_price(offer, catalog) * offer.value / 100, 2)
    if offer.offer_type == OfferType.FLAT_OFF:
        return float(offer.value)
    if offer.offer_type == OfferType.FREE_DELIVERY:
        return float(DELIVERY_COST_INR)
    if offer.offer_type == OfferType.POINTS:
        return round(offer.value * POINT_VALUE_INR, 2)
    return 0.0


def discount_pct_equivalent(offer: Offer, catalog) -> float:
    """The offer expressed as '% off' of its reference price (so ₹100 off ₹1000 = 10%)."""
    ref = reference_price(offer, catalog)
    return round(100 * offer_value_inr(offer, catalog) / ref, 2) if ref else 0.0


def check_discount_limits(offer: Offer, catalog) -> GuardrailResult:
    """Cap + margin floor. Percentage offers must not exceed MAX_DISCOUNT_PCT. Offers tied to
    specific products must leave at least MIN_MARGIN_AFTER_DISCOUNT_PCT margin (in ₹)."""
    if offer.offer_type in PCT_TYPES and offer.value > settings.MAX_DISCOUNT_PCT:
        return GuardrailResult(allowed=False, reason=f"discount {offer.value:g}% is above cap {settings.MAX_DISCOUNT_PCT}%")
    if offer.offer_type in (OfferType.FREE_DELIVERY, OfferType.POINTS):
        return GuardrailResult(allowed=True, reason="ok")

    # Which products' margin pays for this discount? A bundle sells both items together.
    products = [p for p in (offer.product_id, offer.bundle_product_id) if p]
    if not products:
        return GuardrailResult(allowed=True, reason="ok (not tied to a product)")
    if offer.offer_type != OfferType.BUNDLE:
        products = products[:1]
    room_inr = 0.0
    for pid in products:
        price, margin = catalog.price(pid), catalog.margin(pid)
        if price is None:
            return GuardrailResult(allowed=False, reason=f"product {pid} not in catalog")
        room_inr += price * max(0.0, (margin or 0) - MIN_MARGIN_AFTER_DISCOUNT_PCT) / 100
    value = offer_value_inr(offer, catalog)
    if value > room_inr + 0.01:  # +1 paisa for float rounding
        return GuardrailResult(allowed=False, reason=f"₹{value:.0f} discount is below the margin floor (max ₹{room_inr:.0f})")
    return GuardrailResult(allowed=True, reason="ok")


# ---------------------------------------------------------------------------
# Stacking: do two offers apply to the same purchase?
# ---------------------------------------------------------------------------


def offer_scope(offer: Offer, catalog) -> set[str] | None:
    """Leaf categories the offer applies to. None = the whole cart (overlaps everything)."""
    if offer.product_id or offer.bundle_product_id:
        return {c for p in (offer.product_id, offer.bundle_product_id) if p and (c := catalog.category_of(p))}
    if offer.category_id:
        return catalog.leaf_categories(offer.category_id)
    return None


def offers_overlap(a: Offer, b: Offer, catalog) -> bool:
    """True if both offers could be used on the same item (= they would stack)."""
    scope_a, scope_b = offer_scope(a, catalog), offer_scope(b, catalog)
    if scope_a is None or scope_b is None:
        return True
    return bool(scope_a & scope_b)
