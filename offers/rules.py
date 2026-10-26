"""Business rules for offers: discount sizing, money value, margin floor, segments, overlap.

These are small *pure* functions (no database access), shared by the generator, the
guardrails and the analysis. Keeping them in one place means "how big may a discount
be?" has exactly one answer in the whole project.

Limits that the marketing team may change live in `offers/rules.yaml` (also editable on the
dashboard's "Offer rules" page). That file overrides the matching `.env` values and the
defaults below; `refresh_rules()` picks up a saved change without a restart.
"""

from __future__ import annotations

import math
import re
import secrets
from pathlib import Path

import yaml

from common.config import settings
from common.logging_utils import get_logger
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

# Rules from rules.yaml that are not plain limits (see load_rules / apply_rules below).
SKIP_LIKELY_BUYERS: dict = {"enabled": False, "budget_bands": ["high"], "min_orders": 4,
                            "lookback_days": 120, "triggers": ["cart_abandon", "price_drop"]}
CONTROL_GROUP_PCT = 0
AI_CHOICE: dict = {"enabled": True, "shortlist_size": 3}

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


# ---------------------------------------------------------------------------
# Editable rules: offers/rules.yaml (the marketing team's file)
# ---------------------------------------------------------------------------

log = get_logger(__name__)

RULES_FILE = Path(__file__).with_name("rules.yaml")
TRIGGERS = [t.value for t in TriggerType]
BANDS = ("low", "mid", "high")
_loaded_mtime: float | None = None
last_error: str = ""  # shown on the dashboard when rules.yaml has a mistake


def _number(errors: list[str], where: str, value, low: float, high: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{where}: must be a number, got {value!r}")
    elif not low <= value <= high:
        errors.append(f"{where}: must be between {low:g} and {high:g}, got {value:g}")


def _hhmm(errors: list[str], where: str, value) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        errors.append(f'{where}: must be a time like "22:00", got {value!r}')


def validate_rules(data: dict) -> list[str]:
    """Plain-English problems in a rules dict (empty list = the rules are fine)."""
    if not isinstance(data, dict):
        return ["the file is empty or not in the expected format"]
    errors: list[str] = []

    def section(name: str) -> dict:
        value = data.get(name)
        if not isinstance(value, dict):
            errors.append(f"section '{name}' is missing")
            return {}
        return value

    lim = section("limits")
    _number(errors, "limits.max_discount_pct", lim.get("max_discount_pct"), 0, 90)
    _number(errors, "limits.min_margin_after_discount_pct", lim.get("min_margin_after_discount_pct"), 0, 50)
    _number(errors, "limits.approval_threshold_inr", lim.get("approval_threshold_inr"), 0, 1_000_000)
    _number(errors, "limits.daily_discount_budget_inr", lim.get("daily_discount_budget_inr"), 0, 100_000_000)
    freq = section("frequency")
    _number(errors, "frequency.max_offers_per_day", freq.get("max_offers_per_day"), 0, 10)
    _number(errors, "frequency.max_offers_per_week", freq.get("max_offers_per_week"), 0, 50)
    _hhmm(errors, "frequency.quiet_start", freq.get("quiet_start"))
    _hhmm(errors, "frequency.quiet_end", freq.get("quiet_end"))
    start = section("starting_discount_pct")
    valid = section("offer_valid_days")
    for t in TRIGGERS:
        _number(errors, f"starting_discount_pct.{t}", start.get(t), 0, 90)
        _number(errors, f"offer_valid_days.{t}", valid.get(t), 1, 60)
    mult = section("price_sensitivity_multiplier")
    for band in BANDS:
        _number(errors, f"price_sensitivity_multiplier.{band}", mult.get(band), 0, 3)
    skip = section("skip_likely_buyers")
    if not isinstance(skip.get("enabled"), bool):
        errors.append("skip_likely_buyers.enabled: must be true or false")
    if not set(skip.get("budget_bands") or []) <= set(BANDS):
        errors.append(f"skip_likely_buyers.budget_bands: use only {list(BANDS)}")
    if not set(skip.get("triggers") or []) <= set(TRIGGERS):
        errors.append(f"skip_likely_buyers.triggers: use only {TRIGGERS}")
    _number(errors, "skip_likely_buyers.min_orders", skip.get("min_orders"), 1, 100)
    _number(errors, "skip_likely_buyers.lookback_days", skip.get("lookback_days"), 1, 3650)
    _number(errors, "control_group_pct", data.get("control_group_pct"), 0, 50)
    ai = section("ai_choice")
    if not isinstance(ai.get("enabled"), bool):
        errors.append("ai_choice.enabled: must be true or false")
    _number(errors, "ai_choice.shortlist_size", ai.get("shortlist_size"), 1, 10)
    return errors


def load_rules(path: Path | None = None) -> dict:
    """Read rules.yaml as a dict (no validation)."""
    with open(path or RULES_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def apply_rules(data: dict) -> None:
    """Make `data` the live rules. Dicts are updated IN PLACE so modules that imported them see the change."""
    global MIN_MARGIN_AFTER_DISCOUNT_PCT, CONTROL_GROUP_PCT
    lim, freq = data["limits"], data["frequency"]
    settings.MAX_DISCOUNT_PCT = int(lim["max_discount_pct"])
    settings.APPROVAL_THRESHOLD_INR = int(lim["approval_threshold_inr"])
    settings.DAILY_DISCOUNT_BUDGET_INR = int(lim["daily_discount_budget_inr"])
    settings.MAX_OFFERS_PER_DAY = int(freq["max_offers_per_day"])
    settings.MAX_OFFERS_PER_WEEK = int(freq["max_offers_per_week"])
    settings.QUIET_START, settings.QUIET_END = freq["quiet_start"], freq["quiet_end"]
    MIN_MARGIN_AFTER_DISCOUNT_PCT = float(lim["min_margin_after_discount_pct"])
    # Keys stay TriggerType members (an Enum hashes by its name, so "cart_abandon" would be a new key).
    for key in BASE_DISCOUNT_PCT:
        BASE_DISCOUNT_PCT[key] = float(data["starting_discount_pct"][key.value])
    for key in VALID_DAYS:
        VALID_DAYS[key] = int(data["offer_valid_days"][key.value])
    SENSITIVITY_MULTIPLIER.update({b: float(data["price_sensitivity_multiplier"][b]) for b in BANDS})
    SKIP_LIKELY_BUYERS.update(data["skip_likely_buyers"])
    CONTROL_GROUP_PCT = float(data["control_group_pct"])
    AI_CHOICE.update(data["ai_choice"])


def refresh_rules(force: bool = False, path: Path | None = None) -> bool:
    """Re-read rules.yaml if it changed since the last load. A broken file keeps the last good
    rules (and sets `last_error`), so a typo can never switch the limits off. Returns True if applied."""
    global _loaded_mtime, last_error
    path = path or RULES_FILE
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False  # no file: the defaults above stay in force
    if not force and mtime == _loaded_mtime:
        return False
    _loaded_mtime = mtime
    try:
        data = load_rules(path)
    except yaml.YAMLError as exc:
        last_error = f"rules.yaml could not be read: {exc}"
        log.error(last_error)
        return False
    errors = validate_rules(data)
    if errors:
        last_error = "; ".join(errors)
        log.error("rules.yaml has mistakes, keeping the previous rules: %s", last_error)
        return False
    apply_rules(data)
    last_error = ""
    return True


def save_rules(data: dict, path: Path | None = None) -> list[str]:
    """Validate and write rules (used by the dashboard). Returns the problems; writes nothing if any.
    The comments of the hand-written file are kept by replacing only the values, line by line."""
    errors = validate_rules(data)
    if errors:
        return errors
    path = path or RULES_FILE
    lines = path.read_text(encoding="utf-8").splitlines()
    section = None
    for i, line in enumerate(lines):
        top = re.match(r"^(\w+):", line)
        if top:
            section = top.group(1)
            if section in data and not isinstance(data[section], dict):  # e.g. control_group_pct: 10
                lines[i] = _replace_value(line, data[section])
            continue
        sub = re.match(r"^\s+(\w+):", line)
        if sub and isinstance(data.get(section), dict) and sub.group(1) in data[section]:
            lines[i] = _replace_value(line, data[section][sub.group(1)])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    refresh_rules(force=True, path=path)
    return []


def _replace_value(line: str, value) -> str:
    """'  key: old   # comment' -> '  key: new   # comment' (the comment keeps its column)."""
    m = re.match(r"^(\s*\w+:\s*)(.*?)(\s*#.*)?$", line)
    head, old, comment = m.group(1), m.group(2), m.group(3) or ""
    if isinstance(value, str):
        new = f'"{value}"'
    elif isinstance(value, bool):
        new = "true" if value else "false"
    elif isinstance(value, list):
        new = "[" + ", ".join(str(v) for v in value) + "]"
    elif isinstance(value, float) and value.is_integer() and "." not in old:
        new = str(int(value))
    else:
        new = str(value)
    if not comment:
        return head + new
    gap = max(1, len(old) + len(comment) - len(comment.lstrip()) - len(new))
    return head + new + " " * gap + comment.lstrip()


refresh_rules(force=True)
