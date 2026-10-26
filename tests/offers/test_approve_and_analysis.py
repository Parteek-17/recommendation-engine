"""Admin approval CLI logic and the uplift analysis report."""

from datetime import datetime

from common.schemas import RankedOffer
from offers import store
from offers.analysis import build_report, build_send_table, discount_bucket, write_report
from offers.approve import decide, pending_offers
from offers.guardrails import apply_offer_guardrails

from .conftest import audit_rows, execute


def test_pending_lists_high_value_offers(db):
    assert [o.offer_id for o in pending_offers(db)] == ["OF0013"]


def test_approve_lets_offer_through_guardrails(db):
    rank = [RankedOffer(offer_id="OF0013", score=1, reason="t")]
    now = datetime(2026, 10, 1, 12, 0)
    assert apply_offer_guardrails("U00001", rank, now=now, db_path=db) == []
    assert decide("OF0013", approve=True, db_path=db)
    assert not store.get_offer("OF0013", db).requires_approval
    assert pending_offers(db) == []
    assert audit_rows(db, "approve_offer")[-1]["decision"] == "approved"


def test_reject_deactivates(db):
    assert decide("OF0013", approve=False, db_path=db)
    assert not store.get_offer("OF0013", db).is_active
    assert audit_rows(db, "approve_offer")[-1]["decision"] == "rejected"


def test_cannot_decide_non_pending_offer(db):
    assert not decide("OF0001", approve=True, db_path=db)


def test_discount_buckets():
    assert discount_bucket(5) == "small (<10%)"
    assert discount_bucket(10) == "medium (10–20%)"
    assert discount_bucket(20) == "medium (10–20%)"
    assert discount_bucket(25) == "large (>20%)"


def test_send_table_uses_only_delivered_sends(db):
    table = build_send_table(db)
    assert len(table) == 9  # 12 sample sends minus blocked, failed, queued
    assert table["redeemed"].sum() == 1  # SN000004
    assert set(table["sensitivity"]) <= {"high", "medium", "low"}


def test_report_written(db, tmp_path):
    path = write_report(tmp_path / "analysis.md", db_path=db)
    text = path.read_text(encoding="utf-8")
    assert "Redemption rate" in text and "Organic 7-day rate" in text and "Small sample" in text


def test_report_without_history(db):
    execute(db, "DELETE FROM offer_sends")
    assert "No delivered offers yet" in build_report(db)
