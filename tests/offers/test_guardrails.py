"""Offer guardrails + revalidate. Each guardrail has a test proving it blocks the bad case."""

from datetime import datetime

import pytest

from common.config import settings
from common.schemas import RankedOffer
from offers.generator import generate_offers
from offers.guardrails import apply_offer_guardrails, revalidate

from .conftest import add_offer, add_send, audit_rows, execute

NOW = datetime(2026, 10, 1, 12, 0)


def ranked(*offer_ids):
    """Ranked list with descending scores, in the order given."""
    return [RankedOffer(offer_id=o, score=1.0 - i / 100, reason="test") for i, o in enumerate(offer_ids)]


def run(db, user, *offer_ids):
    """Kept offer ids."""
    return [r.offer_id for r in apply_offer_guardrails(user, ranked(*offer_ids), now=NOW, db_path=db)]


def block_reason(db, offer_id):
    rows = [r for r in audit_rows(db, "offer_guardrail") if r["details"].get("offer_id") == offer_id]
    assert rows, f"{offer_id} was not blocked"
    return rows[-1]["reason"]


def test_good_offer_passes(db):
    assert run(db, "U00001", "OF0002") == ["OF0002"]


def test_expired_offer_removed(db):
    assert run(db, "U00001", "OF0018") == []
    assert "expired" in block_reason(db, "OF0018")


def test_inactive_offer_removed(db):
    assert run(db, "U00001", "OF0019") == []
    assert "inactive" in block_reason(db, "OF0019")


def test_not_started_offer_removed(db):
    add_offer(db, "OF9001", start_at="2026-12-01T00:00:00")
    assert run(db, "U00001", "OF9001") == []
    assert "not started" in block_reason(db, "OF9001")


def test_unknown_offer_removed(db):
    assert run(db, "U00001", "OF9999") == []
    assert "not found" in block_reason(db, "OF9999")


def test_unapproved_high_value_offer_removed(db):
    assert run(db, "U00001", "OF0013") == []
    assert "approval" in block_reason(db, "OF0013")


def test_discount_above_cap_removed(db):
    assert run(db, "U00001", "OF0017") == []  # MEGA50 = 50% > 40%
    assert "cap" in block_reason(db, "OF0017")


def test_discount_below_margin_floor_removed(db):
    add_offer(db, "OF9001", value=20, product_id="P00001")  # phone margin is only 5.3%
    assert run(db, "U00001", "OF9001") == []
    assert "margin floor" in block_reason(db, "OF9001")


def test_already_sent_offer_removed(db):
    # U00002 got OF0002 (status 'sent') in the sample data.
    assert run(db, "U00002", "OF0002") == []
    assert "already sent" in block_reason(db, "OF0002")


def test_over_max_uses_removed(db):
    # U00005 redeemed OF0016 (max_uses_per_user = 1).
    assert run(db, "U00005", "OF0016") == []
    assert "already used" in block_reason(db, "OF0016")


def test_redeemed_offer_allowed_again_if_max_uses_higher(db):
    execute(db, "UPDATE offers SET max_uses_per_user = 2 WHERE offer_id = 'OF0016'")
    assert run(db, "U00005", "OF0016") == ["OF0016"]


def test_stacking_with_better_offer_in_same_list_removed(db):
    add_offer(db, "OF9001", category_id="C011", value=10)       # same category as OF0012 (C011)
    assert run(db, "U00001", "OF0012", "OF9001") == ["OF0012"]  # lower-ranked one goes
    assert "stack with OF0012" in block_reason(db, "OF9001")


def test_non_overlapping_offers_both_kept(db):
    assert run(db, "U00001", "OF0012", "OF0009") == ["OF0012", "OF0009"]  # men's clothing vs footwear


def test_stacking_with_offer_user_already_holds_removed(db):
    add_send(db, "SN900001", "U00001", "OF0001", status="opened", sent_at="2026-09-30T10:00:00")  # whole-cart offer
    assert run(db, "U00001", "OF0012") == []
    assert "stack with OF0001" in block_reason(db, "OF0012")


def test_daily_discount_budget_removed(db, monkeypatch):
    monkeypatch.setattr(settings, "DAILY_DISCOUNT_BUDGET_INR", 50)
    assert run(db, "U00001", "OF0002") == []  # 15% off a ~₹800 accessory > ₹50
    assert "budget" in block_reason(db, "OF0002")


def test_budget_counts_offers_already_sent_today_by_everyone(db, monkeypatch):
    monkeypatch.setattr(settings, "DAILY_DISCOUNT_BUDGET_INR", 120)
    assert run(db, "U00001", "OF0003") == ["OF0003"]  # ₹100 fits
    add_send(db, "SN900001", "U00009", "OF0003", sent_at="2026-10-01T08:30:00")  # someone else used ₹100
    assert run(db, "U00001", "OF0003") == []


def test_minor_never_gets_age_restricted_product(db):
    add_offer(db, "OF9001", product_id="P00005", value=5)  # Chef Knife Set, age_restricted
    assert run(db, "U00007", "OF9001") == []                # U00007 is a minor
    assert "minor" in block_reason(db, "OF9001")
    assert run(db, "U00001", "OF9001") == ["OF9001"]        # adults are fine


def test_minor_never_gets_category_with_age_restricted_products(db):
    add_offer(db, "OF9001", category_id="C013", value=5)  # Kitchen Appliances contains the knife set
    assert run(db, "U00007", "OF9001") == []


def test_sensitive_segment_removed(db):
    add_offer(db, "OF9001", segment="female")
    assert run(db, "U00004", "OF9001") == []
    assert "sensitive" in block_reason(db, "OF9001")


def test_user_outside_segment_removed(db):
    assert run(db, "U00003", "OF0004") == []  # FREESHIP is tier3; U00003 lives in Kolkata (tier 1)
    assert "not in segment" in block_reason(db, "OF0004")


def test_personal_offer_only_for_its_user(db):
    # Toys only, so it can't stack with the accessories offer U00002 already holds.
    add_offer(db, "OF9001", segment="user:U00002", category_id="C023")
    assert run(db, "U00001", "OF9001") == []
    assert run(db, "U00002", "OF9001") == ["OF9001"]


def test_summary_is_audited(db):
    run(db, "U00001", "OF0002", "OF0017")
    summary = [r for r in audit_rows(db, "offer_guardrail") if r["decision"] == "allowed"][-1]
    assert summary["details"]["kept"] == ["OF0002"]


# ---------------------------------------------------------------------------
# revalidate
# ---------------------------------------------------------------------------


def reval(db, offer_id, user="U00001", now=NOW):
    return revalidate(offer_id, user, now=now, db_path=db)


def test_revalidate_ok(db):
    assert reval(db, "OF0005").allowed


def test_revalidate_expired_since_ranking(db):
    result = reval(db, "OF0005", now=datetime(2027, 4, 1, 12, 0))
    assert not result.allowed and "no longer active" in result.reason


def test_revalidate_deactivated(db):
    execute(db, "UPDATE offers SET is_active = 0 WHERE offer_id = 'OF0005'")
    assert not reval(db, "OF0005").allowed


def test_revalidate_out_of_stock(db):
    execute(db, "UPDATE products SET stock_qty = 0 WHERE product_id = 'P00002'")  # the cover in PHONECOVER
    result = reval(db, "OF0005")
    assert not result.allowed and "out of stock" in result.reason


def test_revalidate_price_changed(db):
    offer = generate_offers("cart_abandon", "U00004", now=NOW, db_path=db)[0]
    assert reval(db, offer.offer_id, user="U00004").allowed
    execute(db, "UPDATE products SET price = price + 1 WHERE product_id = ?", (offer.product_id,))
    result = reval(db, offer.offer_id, user="U00004")
    assert not result.allowed and "price" in result.reason


def test_revalidate_price_drop_below_margin_floor(db):
    add_offer(db, "OF9001", offer_type="flat_off", value=50, product_id="P00002")  # no snapshot; max ₹71 at ₹799
    assert reval(db, "OF9001").allowed
    execute(db, "UPDATE products SET price = 300 WHERE product_id = 'P00002'")  # ₹50 off ₹300 kills margin
    assert "margin floor" in reval(db, "OF9001").reason


def test_revalidate_unapproved(db):
    assert "approval" in reval(db, "OF0013").reason


def test_revalidate_unknown_offer(db):
    assert reval(db, "OF9999").reason == "offer not found"


def test_revalidate_is_audited(db):
    reval(db, "OF0005")
    assert audit_rows(db, "revalidate")[-1]["decision"] == "allowed"
