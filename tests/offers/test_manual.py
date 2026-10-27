"""Offers made by hand (admin dashboard / admin assistant): same rules as every other offer."""

import pytest

from common.db import get_connection
from offers import manual, store


def test_valid_category_offer_is_allowed_and_described(db, now):
    draft = manual.build_offer("pct_off", 10, category_id="C010", segment="tier3", valid_days=5, now=now)
    assert draft.ok, draft.problems
    assert draft.offer.code and draft.offer.expires_at.day == 6
    assert draft.summary() == "10% off on Mobile Accessories · for tier-3 cities · 5 days"


def test_discount_above_the_cap_is_refused(db, now):
    draft = manual.build_offer("pct_off", 80, category_id="C010", now=now)
    assert not draft.ok
    assert any("maximum discount" in p for p in draft.problems)


def test_product_offer_cannot_cut_into_the_profit_floor(db, now):
    draft = manual.build_offer("pct_off", 30, product_id="P00001", now=now)  # phone with 5.3% margin
    assert not draft.ok
    assert any("profit" in p for p in draft.problems)


def test_whole_cart_flat_offer_needs_a_minimum_cart(db, now):
    assert not manual.build_offer("flat_off", 200, now=now).ok
    assert manual.build_offer("flat_off", 200, min_cart_value=999, now=now).ok


@pytest.mark.parametrize("segment", ["women", "hindu", "everyone_vip"])
def test_sensitive_or_unknown_groups_are_refused(db, now, segment):
    assert not manual.build_offer("free_delivery", segment=segment, now=now).ok


def test_missing_things_are_explained(db, now):
    assert "does not exist" in manual.build_offer("pct_off", 10, product_id="P99999", now=now).problems[0]
    assert "bundle needs" in manual.build_offer("bundle", 10, product_id="P00001", now=now).problems[0]
    assert "valid days" in manual.build_offer("pct_off", 10, valid_days=0, now=now).problems[0]


def test_big_offer_waits_for_approval(db, now):
    draft = manual.build_offer("flat_off", 1500, min_cart_value=5000, now=now)
    assert draft.ok and draft.needs_approval and draft.offer.requires_approval


def test_create_saves_audits_and_can_switch_off(db, now):
    draft = manual.build_offer("pct_off", 12, category_id="C010", code="TEST-12", now=now)
    offer = manual.create_offer(draft, created_by="test")
    saved = store.get_offer(offer.offer_id)
    assert saved and saved.code == "TEST-12" and saved.is_active
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'manual_offer'").fetchone()[0] == 1
    assert manual.deactivate_offer(offer.offer_id)
    assert not store.get_offer(offer.offer_id).is_active
    assert not manual.deactivate_offer(offer.offer_id)  # already off


def test_a_refused_draft_can_never_be_saved(db, now):
    with pytest.raises(ValueError):
        manual.create_offer(manual.build_offer("pct_off", 95, now=now))


def test_duplicate_code_is_refused(db, now):
    manual.create_offer(manual.build_offer("pct_off", 5, code="SAME-CODE", now=now))
    assert "already used" in manual.build_offer("pct_off", 5, code="SAME-CODE", now=now).problems[0]
