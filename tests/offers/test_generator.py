"""Generator: valid offers for all 6 triggers, never below margin floor or above cap."""

import pytest

from common.config import settings
from common.schemas import Offer, OfferType
from offers import generator, store
from offers.generator import generate_offers
from offers.rules import check_discount_limits, margin_cap_pct, offer_value_inr

from .conftest import audit_rows

# (trigger, user, expected offer types) — users chosen from the planted sample-data cases.
CASES = [
    ("cart_abandon", "U00004", {OfferType.PCT_OFF, OfferType.FREE_DELIVERY}),  # left shoes in cart
    ("price_drop", "U00001", {OfferType.FLAT_OFF}),
    ("repurchase_due", "U00002", {OfferType.BUY_AGAIN}),                      # buys atta every ~30 days
    ("post_purchase", "U00001", {OfferType.BUNDLE}),
    ("win_back", "U00001", {OfferType.PCT_OFF}),
    ("festival", None, {OfferType.PCT_OFF}),
]


def assert_safe(offer: Offer, catalog: store.Catalog):
    """No offer above the cap, none below the margin floor."""
    assert check_discount_limits(offer, catalog).allowed, offer
    if offer.offer_type in (OfferType.PCT_OFF, OfferType.BUNDLE, OfferType.BUY_AGAIN):
        assert offer.value <= settings.MAX_DISCOUNT_PCT
        pid = offer.bundle_product_id or offer.product_id
        cap = margin_cap_pct(catalog.margin(pid) if pid else catalog.min_margin(offer.category_id))
        assert offer.value <= cap, f"{offer.offer_id} {offer.value}% > margin cap {cap}%"


@pytest.mark.parametrize("trigger, user, types", CASES)
def test_each_trigger_produces_valid_safe_offers(db, now, trigger, user, types):
    offers = generate_offers(trigger, user, now=now, db_path=db)
    assert offers, f"no offers for {trigger}"
    catalog = store.Catalog.load(db)
    for o in offers:
        assert o.trigger_type == trigger
        assert o.offer_type in types
        assert o.expires_at > o.start_at == now
        assert o.segment == (f"user:{user}" if user and trigger != "festival" else "all")
        assert_safe(o, catalog)
        assert store.get_offer(o.offer_id, db) == o          # saved exactly as returned
    assert len(audit_rows(db, "generate_offer")) == len(offers)


def test_all_triggers_for_all_users_are_safe_and_codes_unique(db, now):
    offers = generate_offers(now=now, db_path=db)
    assert {o.trigger_type for o in offers} == {t for t, _, _ in CASES}
    catalog = store.Catalog.load(db)
    for o in offers:
        assert_safe(o, catalog)
    all_codes = [o.code for o in store.load_offers(db)]
    assert len(all_codes) == len(set(all_codes))
    ids = [o.offer_id for o in offers]
    assert len(ids) == len(set(ids)) and all(i.startswith("OF") for i in ids)


def test_post_purchase_bundle_is_a_complement_not_already_owned(db, now):
    (offer,) = generate_offers("post_purchase", "U00001", now=now, db_path=db)
    assert offer.bundle_product_id != offer.product_id


def test_running_twice_does_not_duplicate(db, now):
    first = generate_offers("cart_abandon", "U00004", now=now, db_path=db)
    assert first and generate_offers("cart_abandon", "U00004", now=now, db_path=db) == []


def test_dry_run_saves_nothing(db, now):
    before = len(store.load_offers(db))
    assert generate_offers("festival", now=now, save=False, db_path=db)
    assert len(store.load_offers(db)) == before


def test_high_value_offers_need_approval(db, now, monkeypatch):
    monkeypatch.setattr(settings, "APPROVAL_THRESHOLD_INR", 100)
    offers = generate_offers("festival", now=now, db_path=db)
    catalog = store.Catalog.load(db)
    assert any(o.requires_approval for o in offers)
    for o in offers:
        assert o.requires_approval == (offer_value_inr(o, catalog) > 100)


def test_price_sensitive_user_gets_bigger_discount(db, now, monkeypatch):
    def pct_for(band):
        monkeypatch.setattr(generator, "budget_band", lambda _uid: band)
        offers = generate_offers("cart_abandon", "U00004", now=now, save=False, db_path=db)
        return max(o.value for o in offers if o.offer_type == OfferType.PCT_OFF)

    assert pct_for("low") > pct_for("mid") > pct_for("high")


def test_out_of_stock_products_get_no_offer(db, now):
    from .conftest import execute

    offers = generate_offers("cart_abandon", "U00004", now=now, save=False, db_path=db)
    for o in offers:
        execute(db, "UPDATE products SET stock_qty = 0 WHERE product_id = ?", (o.product_id,))
    assert generate_offers("cart_abandon", "U00004", now=now, save=False, db_path=db) == []
