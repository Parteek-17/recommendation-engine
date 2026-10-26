"""Steps 5 and 7 of the Offer Pipeline: offer guardrails + last-second re-validation.

    kept = apply_offer_guardrails("U00001", ranked_offers)   # removes unsafe offers, keeps order
    revalidate("OF0021", "U00001")                           # right before sending

"LLM proposes, rules decide": these rules always have the final word on which offer may be
sent. Every removal is written to audit_log with the reason, so we can always answer
"why didn't U00001 get OF0005?".
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from common.config import settings
from common.logging_utils import audit, now_local
from common.schemas import GuardrailResult, Offer, RankedOffer
from offers import store
from offers.rules import check_discount_limits, check_segment, offer_value_inr, offers_overlap, segment_matches
from offers.segments import user_segments

OK = GuardrailResult(allowed=True, reason="ok")


def _block(reason: str) -> GuardrailResult:
    return GuardrailResult(allowed=False, reason=reason)


class _UserState:
    """Everything the guardrails need about one user, loaded once."""

    def __init__(self, user_id: str, now: datetime, db_path):
        self.user_id, self.now, self.db_path = user_id, now, db_path
        self.offers = {o.offer_id: o for o in store.load_offers(db_path)}
        self.catalog = store.Catalog.load(db_path)
        user = store.get_row("users", user_id, db_path) or {}
        self.is_minor = int(user.get("is_minor") or 0) == 1
        self.segments = user_segments(user_id, now, db_path)
        self.sends = store.load_sends(user_id, db_path)

        everyone_today = store.load_sends(None, db_path)
        everyone_today = everyone_today[everyone_today["status"].isin(store.DELIVERED_STATUSES)
                                        & (everyone_today["when"].fillna("").str[:10] == now.date().isoformat())]
        self.spent_today_inr = sum(offer_value_inr(self.offers[oid], self.catalog)
                                   for oid in everyone_today["offer_id"] if oid in self.offers)

    def sends_of(self, offer_id: str, statuses: tuple[str, ...]) -> int:
        s = self.sends
        return int(((s["offer_id"] == offer_id) & s["status"].isin(statuses)).sum())

    def held_offers(self) -> list[Offer]:
        """Offers this user received, hasn't used yet, and that are still valid."""
        ids = self.sends[self.sends["status"].isin(store.OUTSTANDING_STATUSES)]["offer_id"].unique()
        return [self.offers[i] for i in ids if i in self.offers and store.is_live(self.offers[i], self.now)]


# ---------------------------------------------------------------------------
# Individual rules. Each returns OK or a block with a clear reason.
# ---------------------------------------------------------------------------


def rule_active(o: Offer, st: _UserState) -> GuardrailResult:
    if not o.is_active:
        return _block("offer is inactive")
    if st.now < o.start_at:
        return _block("offer has not started yet")
    if st.now > o.expires_at:
        return _block("offer has expired")
    return OK


def rule_approved(o: Offer, st: _UserState) -> GuardrailResult:
    return _block("high-value offer waiting for admin approval") if o.requires_approval else OK


def rule_segment(o: Offer, st: _UserState) -> GuardrailResult:
    allowed = check_segment(o.segment)  # blocks gender/religion/... segments
    if not allowed.allowed:
        return allowed
    return OK if segment_matches(o.segment, st.user_id, st.segments) else _block(f"user is not in segment '{o.segment}'")


def rule_discount_limits(o: Offer, st: _UserState) -> GuardrailResult:
    return check_discount_limits(o, st.catalog)


def rule_age_restricted(o: Offer, st: _UserState) -> GuardrailResult:
    if not st.is_minor:
        return OK
    products = [p for p in (o.product_id, o.bundle_product_id) if p]
    if any(st.catalog.is_age_restricted(p) for p in products):
        return _block("age-restricted product offered to a minor")
    if o.category_id and st.catalog.category_has_age_restricted(o.category_id):
        return _block("category with age-restricted products offered to a minor")
    return OK


def rule_not_already_sent(o: Offer, st: _UserState) -> GuardrailResult:
    return _block("offer already sent to this user") if st.sends_of(o.offer_id, store.OUTSTANDING_STATUSES) else OK


def rule_max_uses(o: Offer, st: _UserState) -> GuardrailResult:
    used = st.sends_of(o.offer_id, ("redeemed",))
    return _block(f"user already used this offer {used}x (max {o.max_uses_per_user})") if used >= o.max_uses_per_user else OK


# Order matters only for which reason is shown first; every offer must pass ALL rules.
RULES = [rule_active, rule_approved, rule_segment, rule_discount_limits, rule_age_restricted,
         rule_not_already_sent, rule_max_uses]


def apply_offer_guardrails(user_id: str, ranked_offers: list[RankedOffer], *, now: datetime | None = None,
                           db_path: str | Path | None = None) -> list[RankedOffer]:
    """Module-contract function: return only the offers that are safe to send, best first.

    Besides the per-offer RULES, two rules look at the whole list:
      - no stacking:   an offer that overlaps one the user already holds, or a better offer
                       kept earlier in this list, is removed
      - daily budget:  total ₹ given away today (all users) must stay under DAILY_DISCOUNT_BUDGET_INR
    """
    st = _UserState(user_id, now or now_local(), db_path)
    kept: list[RankedOffer] = []
    kept_offers: list[Offer] = st.held_offers()
    budget_used = st.spent_today_inr

    for ranked in sorted(ranked_offers, key=lambda r: r.score, reverse=True):
        offer = st.offers.get(ranked.offer_id)
        result = _block("offer not found") if offer is None else OK
        for rule in RULES if offer else []:
            result = rule(offer, st)
            if not result.allowed:
                break
        if result.allowed:
            clash = next((h for h in kept_offers if h.offer_id != offer.offer_id and offers_overlap(offer, h, st.catalog)), None)
            if clash:
                result = _block(f"would stack with {clash.offer_id}")
        if result.allowed:
            value = offer_value_inr(offer, st.catalog)
            if budget_used + value > settings.DAILY_DISCOUNT_BUDGET_INR:
                result = _block(f"daily discount budget exceeded (₹{budget_used:.0f} + ₹{value:.0f} > ₹{settings.DAILY_DISCOUNT_BUDGET_INR})")
            else:
                budget_used += value

        if result.allowed:
            kept.append(ranked)
            kept_offers.append(offer)
        else:
            audit("offers", user_id, "offer_guardrail", "blocked", result.reason,
                  {"offer_id": ranked.offer_id}, db_path=db_path)

    audit("offers", user_id, "offer_guardrail", "allowed" if kept else "none_left",
          f"{len(kept)} of {len(ranked_offers)} offers passed", {"kept": [r.offer_id for r in kept]}, db_path=db_path)
    return kept


def revalidate(offer_id: str, user_id: str, *, now: datetime | None = None,
               db_path: str | Path | None = None) -> GuardrailResult:
    """Module-contract function: last check right before sending.

    Still active + approved, products in stock, price unchanged since the offer was made
    (and the discount still respects the margin floor at today's price), and still OK for a minor.
    """
    now = now or now_local()
    offer = store.get_offer(offer_id, db_path)
    result = _revalidate(offer, user_id, now, db_path) if offer else _block("offer not found")
    audit("offers", user_id, "revalidate", "allowed" if result.allowed else "blocked", result.reason,
          {"offer_id": offer_id}, db_path=db_path)
    return result


def _revalidate(offer: Offer, user_id: str, now: datetime, db_path) -> GuardrailResult:
    if not store.is_live(offer, now):
        return _block("offer is no longer active")
    if offer.requires_approval:
        return _block("high-value offer waiting for admin approval")

    catalog = store.Catalog.load(db_path)
    for pid in (p for p in (offer.product_id, offer.bundle_product_id) if p):
        if not catalog.has(pid):
            return _block(f"product {pid} no longer exists")
        if catalog.stock(pid) <= 0:
            return _block(f"product {pid} is out of stock")

    for pid, old_price in store.get_price_snapshots(offer.offer_id, db_path).items():
        new_price = catalog.price(pid)
        if new_price is not None and abs(new_price - old_price) > 0.005:
            return _block(f"price of {pid} changed (₹{old_price:g} -> ₹{new_price:g})")

    limits = check_discount_limits(offer, catalog)
    if not limits.allowed:
        return limits

    user = store.get_row("users", user_id, db_path) or {}
    if int(user.get("is_minor") or 0) == 1 and any(
            catalog.is_age_restricted(p) for p in (offer.product_id, offer.bundle_product_id) if p):
        return _block("age-restricted product offered to a minor")
    return OK
