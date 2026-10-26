"""The shared pydantic models accept valid data and reject bad data."""

import pytest
from pydantic import ValidationError

from common.schemas import (
    ChatReply,
    EventType,
    GuardrailResult,
    Offer,
    OfferType,
    RankedOffer,
    RecItem,
    TriggerType,
    UserContext,
)


def test_rec_item_valid():
    item = RecItem(product_id="P00001", score=0.9, reason="Because you browse Mobiles", source="item_cf")
    assert item.product_id == "P00001"


def test_rec_item_rejects_bad_score():
    with pytest.raises(ValidationError):
        RecItem(product_id="P00001", score="very high", reason="x", source="x")


def test_offer_parses_enums_and_dates():
    offer = Offer(offer_id="OF0001", code="CART10", offer_type="pct_off", value=10,
                  start_at="2026-09-01T00:00:00", expires_at="2027-03-31T23:59:59", trigger_type="cart_abandon")
    assert offer.offer_type == OfferType.PCT_OFF
    assert offer.trigger_type == TriggerType.CART_ABANDON
    assert offer.expires_at.year == 2027


def test_offer_rejects_unknown_type():
    with pytest.raises(ValidationError):
        Offer(offer_id="OF0001", code="X", offer_type="free_money", value=10,
              start_at="2026-09-01T00:00:00", expires_at="2027-03-31T23:59:59")


def test_other_models():
    assert RankedOffer(offer_id="OF0001", score=0.5, reason="match").score == 0.5
    ctx = UserContext(user_id="U00001", budget_band="mid", top_categories=["C009"])
    assert ctx.is_gift_mode is False and ctx.trigger_type is None
    assert ChatReply(text="hi").product_ids == []
    assert GuardrailResult(allowed=False, reason="cap").allowed is False


def test_enums_compare_to_plain_strings():
    assert EventType.ADD_TO_CART == "add_to_cart"
    assert {e.value for e in EventType} == {"view", "click", "add_to_cart", "remove_from_cart",
                                            "wishlist", "purchase", "search"}
