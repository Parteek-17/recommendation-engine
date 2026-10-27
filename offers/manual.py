"""Offers made by hand: the admin dashboard's offer maker and the admin assistant both use this.

    draft = build_offer(offer_type="pct_off", value=15, category_id="C004", segment="tier3", valid_days=7)
    draft.problems   # [] = allowed by the rules; otherwise plain-English reasons
    create_offer(draft)

Same rules as the automatic Offer Engine (offers/rules.yaml): max discount, profit floor,
allowed segments, approval above the ₹ threshold. A draft with problems can never be saved.
Saved offers go into the normal `offers` table, so the pipeline ranks them, guardrails check
them per customer and Claude may pick them like any other offer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from common.config import settings
from common.db import get_connection
from common.logging_utils import audit, now_local
from common.schemas import Offer, OfferType, TriggerType
from offers import rules, store
from offers.rules import (
    ALLOWED_SEGMENTS,
    PCT_TYPES,
    check_discount_limits,
    check_segment,
    make_code,
    offer_value_inr,
)

MAX_VALID_DAYS = 60
_CODE = re.compile(r"^[A-Z0-9-]{4,20}$")

TYPE_LABELS = {
    "pct_off": "% off",
    "flat_off": "₹ off",
    "free_delivery": "Free delivery",
    "bundle": "Bundle (% off an add-on item)",
    "buy_again": "Buy again (% off a re-order)",
    "points": "Reward points",
}
SEGMENT_LABELS = {
    "all": "Everyone",
    "new_users": "New customers (joined in the last 30 days)",
    "returning": "Returning customers",
    "tier1": "Tier-1 cities",
    "tier2": "Tier-2 cities",
    "tier3": "Tier-3 cities",
    "price_sensitive": "Budget shoppers",
    "premium": "Premium shoppers",
}


@dataclass
class OfferDraft:
    """An offer that is not saved yet, plus what the rules say about it."""

    offer: Offer | None
    problems: list[str] = field(default_factory=list)   # any problem = cannot be saved
    warnings: list[str] = field(default_factory=list)   # allowed, but worth knowing
    value_inr: float = 0.0
    needs_approval: bool = False

    @property
    def ok(self) -> bool:
        return self.offer is not None and not self.problems

    def summary(self) -> str:
        """One line a marketing person understands, e.g. '15% off Footwear for Tier-3 cities, 7 days'."""
        if self.offer is None:
            return "Offer could not be built"
        return describe(self.offer)


def describe(o: Offer, db_path: str | Path | None = None) -> str:
    """Plain-English description of an offer."""
    t = o.offer_type.value
    if t in ("pct_off", "buy_again"):
        what = f"{o.value:g}% off"
    elif t == "flat_off":
        what = f"₹{o.value:,.0f} off"
    elif t == "bundle":
        what = f"{o.value:g}% off the add-on"
    elif t == "points":
        what = f"{o.value:,.0f} reward points"
    else:
        what = "Free delivery"
    if o.product_id:
        where = f" on {_title(o.product_id, db_path)}"
        if o.bundle_product_id:
            where += f" + {_title(o.bundle_product_id, db_path)}"
    elif o.category_id:
        where = f" on {_category_name(o.category_id, db_path)}"
    else:
        where = " on the whole cart"
    cart = f", min. cart ₹{o.min_cart_value:,.0f}" if o.min_cart_value else ""
    segment = o.segment or "all"
    if segment.startswith(rules.USER_SEGMENT_PREFIX):  # personal offer made by the Offer Engine
        who = f"one customer ({segment.removeprefix(rules.USER_SEGMENT_PREFIX).upper()})"
    else:
        who = SEGMENT_LABELS.get(segment, segment).lower()
    days = max(1, round((o.expires_at - o.start_at).total_seconds() / 86400))
    return f"{what}{where}{cart} · for {who} · {days} day{'s' if days != 1 else ''}"


def _title(product_id: str, db_path=None) -> str:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT title FROM products WHERE product_id = ?", (product_id,)).fetchone()
    return row[0] if row else product_id


def _category_name(category_id: str, db_path=None) -> str:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT name FROM categories WHERE category_id = ?", (category_id,)).fetchone()
    return row[0] if row else category_id


def build_offer(
    offer_type: str,
    value: float = 0,
    *,
    category_id: str | None = None,
    product_id: str | None = None,
    bundle_product_id: str | None = None,
    segment: str = "all",
    min_cart_value: float = 0,
    valid_days: int = 7,
    trigger_type: str | None = None,
    code: str | None = None,
    max_uses_per_user: int = 1,
    now: datetime | None = None,
    db_path: str | Path | None = None,
) -> OfferDraft:
    """Build an offer from simple choices and check it against the rules. Never saves anything."""
    rules.refresh_rules()  # the marketing team may have just changed the limits
    now = (now or now_local()).replace(microsecond=0)
    problems: list[str] = []
    warnings: list[str] = []
    catalog = store.Catalog.load(db_path)

    if offer_type not in TYPE_LABELS:
        return OfferDraft(None, [f"unknown offer type '{offer_type}'"])
    kind = OfferType(offer_type)
    value = float(value or 0)
    if kind == OfferType.FREE_DELIVERY:
        value = 0.0
    elif value <= 0:
        problems.append("the offer value must be more than 0")

    product_id, bundle_product_id = product_id or None, bundle_product_id or None
    category_id = category_id or None
    if product_id and not catalog.has(product_id):
        problems.append(f"product {product_id} does not exist")
    if bundle_product_id and not catalog.has(bundle_product_id):
        problems.append(f"add-on product {bundle_product_id} does not exist")
    if category_id and _category_name(category_id, db_path) == category_id:  # no name found
        problems.append(f"category {category_id} does not exist")
    if kind == OfferType.BUNDLE and not (product_id and bundle_product_id):
        problems.append("a bundle needs a main product and an add-on product")
    if kind == OfferType.BUY_AGAIN and not product_id:
        problems.append("'buy again' needs the product people re-order")
    if product_id and category_id:
        category_id = None  # a product offer is already more specific

    segment = segment or "all"
    seg = check_segment(segment)
    if not seg.allowed:
        problems.append(seg.reason)
    elif segment not in ALLOWED_SEGMENTS:
        problems.append(f"'{segment}' is not one of the customer groups: {', '.join(sorted(ALLOWED_SEGMENTS))}")

    if not 1 <= int(valid_days) <= MAX_VALID_DAYS:
        problems.append(f"valid days must be between 1 and {MAX_VALID_DAYS}")
    trigger = None
    if trigger_type:
        try:
            trigger = TriggerType(trigger_type)
        except ValueError:
            problems.append(f"unknown reason '{trigger_type}'")

    taken = store.existing_codes(db_path)
    if code:
        code = code.strip().upper()
        if not _CODE.match(code):
            problems.append("coupon code: 4–20 capital letters, digits or '-'")
        elif code in taken:
            problems.append(f"coupon code {code} is already used")
    else:
        code = make_code(trigger or "manual", value, taken)

    if problems:
        return OfferDraft(None, problems)

    offer = Offer(
        offer_id="NEW", code=code, offer_type=kind, value=value, min_cart_value=float(min_cart_value or 0),
        category_id=category_id, product_id=product_id, bundle_product_id=bundle_product_id,
        segment=segment, trigger_type=trigger, max_uses_per_user=max(1, int(max_uses_per_user)),
        start_at=now, expires_at=now + timedelta(days=int(valid_days)), is_active=True, requires_approval=False,
    )

    limits = check_discount_limits(offer, catalog)
    if not limits.allowed:
        problems.append(_friendly(limits.reason))
    value_inr = offer_value_inr(offer, catalog)

    if kind == OfferType.FLAT_OFF:
        price = catalog.price(product_id) if product_id else None
        if price is not None and value >= price:
            problems.append(f"₹{value:,.0f} off is more than the product's price (₹{price:,.0f})")
        elif not product_id and offer.min_cart_value < 2 * value:
            problems.append(f"set a minimum cart of at least ₹{2 * value:,.0f} so the discount is "
                            "at most half of what the customer pays")
    if kind in PCT_TYPES and category_id and not product_id:
        room = (catalog.min_margin(category_id) or 0) - rules.MIN_MARGIN_AFTER_DISCOUNT_PCT
        if value > room:
            warnings.append(f"some products in this category earn less than {value:g}% + the profit floor; "
                            "the discount still applies to them")
    age_limited = (catalog.is_age_restricted(product_id) if product_id
                   else catalog.category_has_age_restricted(category_id) if category_id else False)
    if age_limited:
        warnings.append("has age-restricted products: customers under 18 will never receive it")

    needs_approval = value_inr > settings.APPROVAL_THRESHOLD_INR
    if needs_approval:
        offer.requires_approval = True
        warnings.append(f"worth ₹{value_inr:,.0f} per use, above the ₹{settings.APPROVAL_THRESHOLD_INR:,.0f} "
                        "approval limit: it waits for a manager's OK before anyone gets it")
    return OfferDraft(offer, problems, warnings, value_inr, needs_approval)


def _friendly(reason: str) -> str:
    """Guardrail wording -> marketing wording."""
    if "above cap" in reason:
        return reason.replace("is above cap", "is above the maximum discount of") + " (Offer rules page)"
    if "margin floor" in reason:
        return reason.replace("discount is below the margin floor", "discount would cut into the profit we must keep")
    return reason


def create_offer(draft: OfferDraft, created_by: str = "admin", db_path: str | Path | None = None) -> Offer:
    """Save a checked draft. Raises ValueError if the draft has problems (it can never be forced)."""
    if not draft.ok:
        raise ValueError("offer not allowed: " + "; ".join(draft.problems))
    # Check again at save time: rules or codes may have changed since the draft was shown.
    o = draft.offer
    fresh = build_offer(
        o.offer_type.value, o.value, category_id=o.category_id, product_id=o.product_id,
        bundle_product_id=o.bundle_product_id, segment=o.segment or "all", min_cart_value=o.min_cart_value,
        valid_days=max(1, round((o.expires_at - o.start_at).total_seconds() / 86400)),
        trigger_type=o.trigger_type.value if o.trigger_type else None, code=o.code,
        max_uses_per_user=o.max_uses_per_user, now=o.start_at, db_path=db_path,
    )
    if not fresh.ok:
        raise ValueError("offer not allowed: " + "; ".join(fresh.problems))
    offer = fresh.offer.model_copy(update={"offer_id": store.next_offer_ids(1, db_path)[0]})
    store.insert_offers([offer], db_path)
    store.save_price_snapshots([offer], store.Catalog.load(db_path), offer.start_at, db_path)
    audit("offers", None, "manual_offer", "pending_approval" if offer.requires_approval else "created",
          f"by {created_by}: {describe(offer, db_path)}",
          {"offer_id": offer.offer_id, "code": offer.code, "value_inr": fresh.value_inr}, db_path=db_path)
    return offer


def deactivate_offer(offer_id: str, db_path: str | Path | None = None) -> bool:
    """Switch an offer off (it stays in the history). False if it doesn't exist or is already off."""
    with get_connection(db_path) as conn:
        changed = conn.execute("UPDATE offers SET is_active = 0 WHERE offer_id = ? AND is_active = 1",
                               (offer_id,)).rowcount > 0
    if changed:
        audit("offers", None, "deactivate_offer", "done", "admin switched it off", {"offer_id": offer_id},
              db_path=db_path)
    return changed
