"""Offer generator: creates offers with simple, explainable rules (one builder per trigger).

    cart_abandon   -> pct_off on the items left in the cart (free_delivery if margin is too thin)
    price_drop     -> flat_off (₹) on a wishlisted item
    repurchase_due -> buy_again on a consumable that is due to run out
    post_purchase  -> bundle: discount on a complement of the last purchase
    win_back       -> pct_off on the user's favourite category
    festival       -> category-wide pct_off for everyone

Discount sizing (rules.size_discount_pct): base % per trigger x price-sensitivity
multiplier, never above MAX_DISCOUNT_PCT and never below the product's margin floor.
Offers worth more than APPROVAL_THRESHOLD_INR get requires_approval=1.

Personal offers get segment "user:<user_id>" so only that user ever sees them.

Usage:
    from offers.generator import generate_offers
    new = generate_offers("cart_abandon", user_id="U00004")
    python -m offers.generator festival            # from the terminal
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from common.config import settings
from common.db import get_connection, init_db
from common.logging_utils import audit, get_logger, now_local
from common.schemas import Offer, OfferType, TriggerType
from offers import store
from offers.rules import (
    MIN_USEFUL_PCT,
    USER_SEGMENT_PREFIX,
    VALID_DAYS,
    check_discount_limits,
    make_code,
    margin_cap_pct,
    offer_value_inr,
    size_discount_pct,
)
from offers.segments import budget_band

try:
    from reco import build_user_context, get_complements
except ImportError:
    from common.stubs import build_user_context, get_complements

log = get_logger(__name__)

MAX_ITEMS_PER_USER = 2      # at most this many product offers per user per trigger
FESTIVAL_CATEGORIES = 5     # festival without a user: this many most popular categories
FESTIVAL_MIN_CART = 499     # festival offers need a small minimum order


class _Data:
    """Everything the builders need, read once from the DB."""

    def __init__(self, db_path: str | Path | None, user_id: str | None = None):
        init_db(db_path)
        self.db_path = db_path
        self.catalog = store.Catalog.load(db_path)
        # With a user_id we only need THAT user's events. Loading everyone's (and grouping them
        # per user) took tens of seconds per call on the full 1M-event data (found in Phase 7).
        where, params = "WHERE event_type IN ('add_to_cart', 'remove_from_cart', 'wishlist', 'purchase')", ()
        if user_id:
            where, params = where + " AND user_id = ?", (user_id,)
        with get_connection(db_path) as conn:
            self.events = pd.read_sql_query(
                f"SELECT user_id, product_id, event_type, timestamp FROM events {where}", conn, params=params)
            self.minors = {r[0] for r in conn.execute("SELECT user_id FROM users WHERE is_minor = 1")}
            self.opted_in = [r[0] for r in conn.execute(
                "SELECT user_id FROM user_consent WHERE opted_in = 1 ORDER BY user_id")]
        self._by_user = dict(tuple(self.events.groupby("user_id"))) if not self.events.empty else {}
        self._popular_categories: list[str] | None = None

    @property
    def popular_categories(self) -> list[str]:
        """Categories by number of events (only the store-wide festival offers need this; it scans every event)."""
        if self._popular_categories is None:
            with get_connection(self.db_path) as conn:
                self._popular_categories = [r[0] for r in conn.execute(
                    "SELECT p.category_id FROM events e JOIN products p ON p.product_id = e.product_id "
                    "GROUP BY p.category_id ORDER BY COUNT(*) DESC")]
        return self._popular_categories

    def user_events(self, user_id: str) -> pd.DataFrame:
        return self._by_user.get(user_id, self.events.iloc[0:0])

    def allowed_for(self, user_id: str | None, product_id: str) -> bool:
        """In stock, and not age-restricted for a minor."""
        if not self.catalog.has(product_id) or self.catalog.stock(product_id) <= 0:
            return False
        return not (user_id in self.minors and self.catalog.is_age_restricted(product_id))


def _draft(trigger: str, offer_type: OfferType, value: float, user_id: str | None, **fields) -> dict:
    """An offer without id/code/dates yet (those are filled in by _finalize)."""
    segment = f"{USER_SEGMENT_PREFIX}{user_id}" if user_id else "all"
    return {"trigger_type": trigger, "offer_type": offer_type, "value": value, "segment": segment, **fields}


def _not_bought_after(ev: pd.DataFrame, event_type: str) -> list[str]:
    """Products with `event_type` (e.g. add_to_cart) that were NOT purchased or removed afterwards.
    Most recent first."""
    last_action = ev[ev["event_type"] == event_type].groupby("product_id")["timestamp"].max()
    closed = ev[ev["event_type"].isin(["purchase", "remove_from_cart"])].groupby("product_id")["timestamp"].max()
    still_open = [pid for pid, ts in last_action.items() if not (pid in closed and closed[pid] >= ts)]
    return sorted(still_open, key=lambda pid: last_action[pid], reverse=True)


# ---------------------------------------------------------------------------
# One builder per trigger. Each returns a list of drafts for ONE user (or for everyone).
# ---------------------------------------------------------------------------


def _cart_abandon(d: _Data, user_id: str, now: datetime) -> list[dict]:
    band, drafts = budget_band(user_id), []
    for pid in _not_bought_after(d.user_events(user_id), "add_to_cart"):
        if not d.allowed_for(user_id, pid):
            continue
        pct = size_discount_pct(TriggerType.CART_ABANDON, band, margin_cap_pct(d.catalog.margin(pid)))
        if pct >= MIN_USEFUL_PCT:
            drafts.append(_draft(TriggerType.CART_ABANDON, OfferType.PCT_OFF, pct, user_id, product_id=pid))
        else:  # margin too thin for a real discount -> free delivery costs us less
            drafts.append(_draft(TriggerType.CART_ABANDON, OfferType.FREE_DELIVERY, 0, user_id, product_id=pid))
        if len(drafts) >= MAX_ITEMS_PER_USER:
            break
    return drafts


def _price_drop(d: _Data, user_id: str, now: datetime) -> list[dict]:
    band, drafts = budget_band(user_id), []
    for pid in _not_bought_after(d.user_events(user_id), "wishlist"):
        if not d.allowed_for(user_id, pid):
            continue
        pct = size_discount_pct(TriggerType.PRICE_DROP, band, margin_cap_pct(d.catalog.margin(pid)))
        rupees = (d.catalog.price(pid) * pct / 100) // 10 * 10  # round DOWN to ₹10
        if rupees >= 10:
            drafts.append(_draft(TriggerType.PRICE_DROP, OfferType.FLAT_OFF, rupees, user_id, product_id=pid))
        if len(drafts) >= MAX_ITEMS_PER_USER:
            break
    return drafts


def _repurchase_due(d: _Data, user_id: str, now: datetime) -> list[dict]:
    """Consumables whose last purchase + repurchase_days falls within the next week (or is overdue)."""
    ev = d.user_events(user_id)
    last_buy = ev[ev["event_type"] == "purchase"].groupby("product_id")["timestamp"].max()
    due = []
    for pid, ts in last_buy.items():
        if not (d.catalog.is_consumable(pid) and d.allowed_for(user_id, pid)):
            continue
        days = d.catalog.repurchase_days(pid)
        due_at = datetime.fromisoformat(ts) + timedelta(days=days) if days else None
        if due_at and due_at <= now + timedelta(days=7):
            due.append((due_at, pid))
    band, drafts = budget_band(user_id), []
    for _, pid in sorted(due, reverse=True)[:MAX_ITEMS_PER_USER]:  # most recently due first
        pct = size_discount_pct(TriggerType.REPURCHASE_DUE, band, margin_cap_pct(d.catalog.margin(pid)))
        if pct >= 1:
            drafts.append(_draft(TriggerType.REPURCHASE_DUE, OfferType.BUY_AGAIN, pct, user_id, product_id=pid))
    return drafts


def _post_purchase(d: _Data, user_id: str, now: datetime) -> list[dict]:
    """Bundle offer: the item just bought + a discount on something that goes with it (never a substitute)."""
    ev = d.user_events(user_id)
    purchases = ev[ev["event_type"] == "purchase"].sort_values("timestamp")
    if purchases.empty:
        return []
    bought_pid, owned = purchases.iloc[-1]["product_id"], set(purchases["product_id"])
    try:
        complements = get_complements(bought_pid, k=10)
    except Exception as exc:
        log.warning("get_complements(%s) failed: %s", bought_pid, exc)
        return []
    band = budget_band(user_id)
    for rec in complements:
        pid = rec.product_id
        if pid in owned or not d.allowed_for(user_id, pid):
            continue
        # Sized on the complement's margin alone: the first item is already paid for.
        pct = size_discount_pct(TriggerType.POST_PURCHASE, band, margin_cap_pct(d.catalog.margin(pid)))
        if pct >= MIN_USEFUL_PCT:
            return [_draft(TriggerType.POST_PURCHASE, OfferType.BUNDLE, pct, user_id,
                           product_id=bought_pid, bundle_product_id=pid)]
    return []


def _win_back(d: _Data, user_id: str, now: datetime) -> list[dict]:
    try:
        top = build_user_context(user_id).top_categories
    except Exception as exc:
        log.warning("build_user_context(%s) failed: %s", user_id, exc)
        return []
    band = budget_band(user_id)
    for cat in top:
        if user_id in d.minors and d.catalog.category_has_age_restricted(cat):
            continue
        pct = size_discount_pct(TriggerType.WIN_BACK, band, margin_cap_pct(d.catalog.min_margin(cat)))
        if pct >= MIN_USEFUL_PCT:
            return [_draft(TriggerType.WIN_BACK, OfferType.PCT_OFF, pct, user_id, category_id=cat)]
    return []


def _festival(d: _Data, user_id: str | None, now: datetime) -> list[dict]:
    """Category-wide, same for everyone (segment 'all'), so no per-user sizing.
    With a user: their favourite categories; without: the most popular ones."""
    cats: list[str] = []
    if user_id:
        try:
            cats = build_user_context(user_id).top_categories
        except Exception:
            pass
    cats = cats or d.popular_categories
    drafts = []
    for cat in cats:
        pct = size_discount_pct(TriggerType.FESTIVAL, None, margin_cap_pct(d.catalog.min_margin(cat)))
        if pct >= MIN_USEFUL_PCT:
            drafts.append(_draft(TriggerType.FESTIVAL, OfferType.PCT_OFF, pct, None,
                                 category_id=cat, min_cart_value=FESTIVAL_MIN_CART))
        if len(drafts) >= (2 if user_id else FESTIVAL_CATEGORIES):
            break
    return drafts


_BUILDERS = {
    TriggerType.CART_ABANDON: _cart_abandon,
    TriggerType.PRICE_DROP: _price_drop,
    TriggerType.REPURCHASE_DUE: _repurchase_due,
    TriggerType.POST_PURCHASE: _post_purchase,
    TriggerType.WIN_BACK: _win_back,
}

# ---------------------------------------------------------------------------
# Finalize: ids, codes, dates, approval flag, last safety check, save
# ---------------------------------------------------------------------------

# Two offers with the same "shape" are duplicates (we don't create the same offer twice while one is live).
_SHAPE = ("trigger_type", "offer_type", "product_id", "bundle_product_id", "category_id", "segment")


def _shape(o: Offer | dict) -> tuple:
    get = o.get if isinstance(o, dict) else (lambda k: getattr(o, k))
    return tuple(str(get(k).value if hasattr(get(k), "value") else get(k) or "") for k in _SHAPE)


def _finalize(drafts: list[dict], d: _Data, now: datetime, save: bool) -> list[Offer]:
    live_shapes = {_shape(o) for o in store.load_offers(d.db_path) if store.is_live(o, now)}
    unique, seen = [], set()
    for dr in drafts:
        if _shape(dr) in live_shapes or _shape(dr) in seen:
            continue  # same offer already live -> don't spam duplicates
        seen.add(_shape(dr))
        unique.append(dr)

    ids, codes = store.next_offer_ids(len(unique), d.db_path), store.existing_codes(d.db_path)
    offers = []
    for offer_id, dr in zip(ids, unique):
        offer = Offer(
            offer_id=offer_id, code=make_code(dr["trigger_type"], dr["value"], codes),
            min_cart_value=dr.pop("min_cart_value", 0), max_uses_per_user=1,
            start_at=now.replace(microsecond=0), expires_at=(now + timedelta(days=VALID_DAYS[dr["trigger_type"]])).replace(microsecond=0),
            is_active=True, requires_approval=False, **dr,
        )
        # Belt and braces: the builders size correctly, but nothing unsafe may ever be saved.
        limits = check_discount_limits(offer, d.catalog)
        if not limits.allowed:
            log.error("Generator produced an unsafe offer, dropped: %s (%s)", offer.model_dump(), limits.reason)
            continue
        if offer_value_inr(offer, d.catalog) > settings.APPROVAL_THRESHOLD_INR:
            offer.requires_approval = True
        offers.append(offer)

    if save and offers:
        store.insert_offers(offers, d.db_path)
        store.save_price_snapshots(offers, d.catalog, now, d.db_path)
        for o in offers:
            audit("offers", o.segment.removeprefix(USER_SEGMENT_PREFIX) if o.segment != "all" else None,
                  "generate_offer", "pending_approval" if o.requires_approval else "created",
                  f"{o.trigger_type.value}: {o.offer_type.value} {o.value:g}",
                  {"offer_id": o.offer_id, "code": o.code, "value_inr": offer_value_inr(o, d.catalog)},
                  db_path=d.db_path)
    return offers


def generate_offers(trigger_type: str | None = None, user_id: str | None = None, *,
                    now: datetime | None = None, save: bool = True,
                    db_path: str | Path | None = None) -> list[Offer]:
    """Create new offers for one trigger (or all six when trigger_type is None).

    user_id: make personal offers for this user. Without it, personal triggers run for every
             opted-in user (only they can receive offers anyway).
    save:    False = just return what would be created (a dry run).
    Returns only the NEW offers (an identical offer that is already live is not created again).
    """
    now = now or now_local()
    triggers = [TriggerType(trigger_type)] if trigger_type else list(TriggerType)
    d = _Data(db_path, user_id)
    drafts: list[dict] = []
    for trig in triggers:
        if trig == TriggerType.FESTIVAL:
            drafts += _festival(d, user_id, now)
            continue
        for uid in ([user_id] if user_id else d.opted_in):
            drafts += _BUILDERS[trig](d, uid, now)
    offers = _finalize(drafts, d, now, save)
    log.info("Generated %d new offer(s) for trigger=%s user=%s", len(offers), trigger_type or "all", user_id or "all")
    return offers


def main() -> None:
    parser = argparse.ArgumentParser(description="Create offers with the rule-based generator.")
    parser.add_argument("trigger_type", nargs="?", choices=[t.value for t in TriggerType], help="default: all six")
    parser.add_argument("--user", help="make personal offers for this user only")
    parser.add_argument("--dry-run", action="store_true", help="show the offers but don't save them")
    args = parser.parse_args()
    offers = generate_offers(args.trigger_type, args.user, save=not args.dry_run)
    for o in offers:
        flag = "  (needs approval)" if o.requires_approval else ""
        print(f"{o.offer_id}  {o.code:<16} {o.trigger_type.value:<15} {o.offer_type.value:<13} "
              f"value={o.value:<6g} product={o.product_id or '-'} category={o.category_id or '-'} "
              f"segment={o.segment}{flag}")
    print(f"{len(offers)} offer(s) {'would be ' if args.dry_run else ''}created.")


if __name__ == "__main__":
    main()
