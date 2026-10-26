"""Pure business rules: sizing, margin floor, segments, overlap."""

from datetime import datetime

import pytest

from common.config import settings
from common.schemas import Offer
from offers import store
from offers.rules import (
    check_discount_limits,
    check_segment,
    margin_cap_pct,
    offer_value_inr,
    offers_overlap,
    segment_matches,
    size_discount_pct,
)


def make_offer(**fields) -> Offer:
    base = {"offer_id": "OF9000", "code": "X", "offer_type": "pct_off", "value": 10,
            "start_at": datetime(2026, 9, 1), "expires_at": datetime(2027, 1, 1)}
    return Offer(**{**base, **fields})


@pytest.fixture
def catalog(db):
    return store.Catalog.load(db)


def test_price_sensitive_users_get_bigger_discounts():
    low, mid, high = (size_discount_pct("festival", band, cap_pct=40) for band in ("low", "mid", "high"))
    assert low > mid > high


def test_discount_never_above_margin_cap_or_global_cap(monkeypatch):
    assert size_discount_pct("festival", "low", cap_pct=3.3) == 3   # margin floor wins (rounded down)
    monkeypatch.setattr(settings, "MAX_DISCOUNT_PCT", 12)
    assert size_discount_pct("festival", "low", cap_pct=40) == 12   # global cap wins


def test_margin_cap_keeps_two_points_of_margin():
    assert margin_cap_pct(5.3) == pytest.approx(3.3)
    assert margin_cap_pct(1.0) == 0          # thinner than the floor -> no discount
    assert margin_cap_pct(None) == 0         # unknown margin -> no discount


@pytest.mark.parametrize("segment", ["female", "gender:m", "women_25_34", "religion_hindu", "Muslim", "caste-x"])
def test_sensitive_segments_are_blocked(segment):
    result = check_segment(segment)
    assert not result.allowed and "sensitive" in result.reason


@pytest.mark.parametrize("segment", ["all", "tier3", "new_users", "price_sensitive", "user:U00001", None])
def test_allowed_segments(segment):
    assert check_segment(segment).allowed


def test_unknown_segment_is_blocked():
    assert not check_segment("vip_gold").allowed


def test_segment_matches_personal_offer_only_for_that_user():
    assert segment_matches("user:U00001", "U00001", set())
    assert not segment_matches("user:U00001", "U00002", {"all"})
    assert segment_matches("tier3", "U00001", {"all", "tier3"})


def test_margin_floor_blocks_too_big_discount_on_thin_margin_product(catalog):
    # P00001 phone: price 15999, margin 5.3% -> at most 3.3% off.
    assert not check_discount_limits(make_offer(product_id="P00001", value=10), catalog).allowed
    assert check_discount_limits(make_offer(product_id="P00001", value=3), catalog).allowed
    assert not check_discount_limits(make_offer(offer_type="flat_off", product_id="P00001", value=1000), catalog).allowed


def test_cap_blocks_pct_above_max(catalog):
    result = check_discount_limits(make_offer(value=settings.MAX_DISCOUNT_PCT + 1), catalog)
    assert not result.allowed and "cap" in result.reason


def test_offer_value_inr(catalog):
    assert offer_value_inr(make_offer(product_id="P00002", value=10), catalog) == pytest.approx(79.9)
    assert offer_value_inr(make_offer(offer_type="flat_off", value=150), catalog) == 150
    assert offer_value_inr(make_offer(offer_type="free_delivery", value=0), catalog) > 0


def test_overlap(catalog):
    global_offer = make_offer()
    phones = make_offer(category_id="C009")
    electronics = make_offer(category_id="C001")          # parent of C009 and C010
    shoes = make_offer(category_id="C012")
    phone = make_offer(product_id="P00001")
    assert offers_overlap(global_offer, shoes, catalog)    # whole-cart offer overlaps everything
    assert offers_overlap(phones, electronics, catalog)
    assert offers_overlap(phone, phones, catalog)
    assert not offers_overlap(phones, shoes, catalog)
