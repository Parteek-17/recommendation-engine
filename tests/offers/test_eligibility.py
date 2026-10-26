"""Eligibility: consent, Telegram link, quiet hours, daily/weekly caps, segment match."""

from datetime import datetime

import pytest

from common.config import settings
from offers.eligibility import check_eligibility, in_quiet_hours

from .conftest import add_send, audit_rows, execute


def check(db, user="U00001", trigger="cart_abandon", now=datetime(2026, 10, 1, 12, 0)):
    return check_eligibility(user, trigger, now=now, db_path=db)


def test_eligible_user(db):
    result = check(db)
    assert result.allowed, result.reason


def test_not_opted_in_is_blocked(db):
    result = check(db, user="U00007")  # opted_in = 0
    assert not result.allowed and "opted in" in result.reason


def test_not_linked_to_telegram_is_blocked(db):
    assert "Telegram" in check(db, user="U00005").reason   # opted in, never linked
    assert "Telegram" in check(db, user="U00004").reason   # got a token but never pressed Start


@pytest.mark.parametrize("hour, minute", [(22, 0), (23, 30), (2, 0), (7, 59)])
def test_quiet_hours_block(db, hour, minute):
    result = check(db, now=datetime(2026, 10, 1, hour, minute))
    assert not result.allowed and "quiet" in result.reason


def test_quiet_hours_logic():
    assert in_quiet_hours(datetime(2026, 1, 1, 23, 0), "22:00", "08:00")
    assert not in_quiet_hours(datetime(2026, 1, 1, 8, 0), "22:00", "08:00")
    assert in_quiet_hours(datetime(2026, 1, 1, 14, 0), "13:00", "15:00")   # same-day window
    assert not in_quiet_hours(datetime(2026, 1, 1, 16, 0), "13:00", "15:00")


def test_daily_cap_blocks(db):
    add_send(db, "SN900001", "U00001", "OF0002", status="sent", sent_at="2026-10-01T09:00:00")
    result = check(db)
    assert not result.allowed and "daily cap" in result.reason


def test_blocked_and_failed_sends_do_not_count(db):
    add_send(db, "SN900001", "U00001", "OF0002", status="blocked", sent_at="2026-10-01T09:00:00")
    add_send(db, "SN900002", "U00001", "OF0003", status="failed", sent_at="2026-10-01T10:00:00")
    assert check(db).allowed


def test_weekly_cap_blocks(db):
    for i, day in enumerate(["2026-09-26", "2026-09-28", "2026-09-30"]):  # 3 in the last 7 days
        add_send(db, f"SN90000{i}", "U00001", "OF0002", sent_at=f"{day}T10:00:00")
    result = check(db)
    assert not result.allowed and "weekly cap" in result.reason


def test_user_can_choose_a_lower_weekly_limit(db):
    execute(db, "UPDATE user_consent SET max_offers_per_week = 1 WHERE user_id = 'U00001'")
    add_send(db, "SN900001", "U00001", "OF0002", sent_at="2026-09-29T10:00:00")
    assert "weekly cap reached (1/1)" in check(db).reason


def test_user_cannot_raise_the_weekly_limit(db):
    execute(db, "UPDATE user_consent SET max_offers_per_week = 99 WHERE user_id = 'U00001'")
    for i, day in enumerate(["2026-09-26", "2026-09-28", "2026-09-30"]):
        add_send(db, f"SN90000{i}", "U00001", "OF0002", sent_at=f"{day}T10:00:00")
    assert f"/{settings.MAX_OFFERS_PER_WEEK})" in check(db).reason


def test_no_offer_for_users_segment_blocks(db):
    # Only tier-1 cart offers exist now; U00001 lives in a tier-3 city.
    execute(db, "UPDATE offers SET segment = 'tier1' WHERE trigger_type = 'cart_abandon'")
    result = check(db)
    assert not result.allowed and "segment" in result.reason


def test_unknown_trigger_blocked(db):
    assert "unknown trigger" in check(db, trigger="birthday").reason


def test_every_decision_is_audited(db):
    check(db)
    check(db, user="U00007")
    assert [r["decision"] for r in audit_rows(db, "check_eligibility")] == ["allowed", "blocked"]
