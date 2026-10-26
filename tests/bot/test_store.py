"""DB helpers: quiet hours, IDs, events and offer_sends status rules."""

from datetime import datetime

from bot import store
from tests.bot.conftest import NOON, query


def test_quiet_hours_across_midnight():
    assert store.in_quiet_hours(datetime(2026, 10, 1, 23, 0), "22:00", "08:00")
    assert store.in_quiet_hours(datetime(2026, 10, 1, 7, 59), "22:00", "08:00")
    assert not store.in_quiet_hours(datetime(2026, 10, 1, 8, 0), "22:00", "08:00")
    assert not store.in_quiet_hours(datetime(2026, 10, 1, 12, 0), "22:00", "08:00")


def test_quiet_hours_same_day_window_and_empty_window():
    assert store.in_quiet_hours(datetime(2026, 10, 1, 14, 0), "13:00", "15:00")
    assert not store.in_quiet_hours(datetime(2026, 10, 1, 16, 0), "13:00", "15:00")
    assert not store.in_quiet_hours(datetime(2026, 10, 1, 16, 0), "10:00", "10:00")


def test_log_event_uses_next_id_and_telegram_source(bot_db):
    last = query("SELECT MAX(event_id) AS m FROM events")[0]["m"]
    event_id = store.log_event("U00001", "P00001", "click", now=NOON)
    assert int(event_id[1:]) == int(last[1:]) + 1
    row = query("SELECT * FROM events WHERE event_id = ?", (event_id,))[0]
    assert row["source"] == "telegram" and row["event_type"] == "click" and row["user_id"] == "U00001"
    # A second click soon after belongs to the same Telegram session.
    second = store.log_event("U00001", "P00002", "click", now=NOON.replace(minute=10))
    sessions = {r["session_id"] for r in query("SELECT session_id FROM events WHERE event_id IN (?, ?)",
                                                 (event_id, second))}
    assert len(sessions) == 1


def test_send_status_only_moves_forward(bot_db):
    send_id = store.create_offer_send("U00001", "OF0001", "hi", "cart_abandon", now=NOON)
    assert send_id.startswith("SN")
    assert store.set_send_status(send_id, "sent", now=NOON)
    assert store.set_send_status(send_id, "redeemed")
    assert not store.set_send_status(send_id, "opened")  # never go backwards
    assert store.get_send(send_id)["status"] == "redeemed"


def test_gift_mode_toggle(bot_db):
    assert not store.get_gift_mode("U00001")
    store.set_gift_mode("U00001", True)
    assert store.get_gift_mode("U00001")
