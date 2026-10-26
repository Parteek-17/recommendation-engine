"""Sending: rate limits, Telegram errors (blocked / RetryAfter / network) and final checks.

Telegram is always mocked (FakeBot); time is faked (FakeSleep / fake clocks).
"""

import asyncio

import pytest
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

from bot import sender, store
from bot.sender import ChatRateLimiter, RateLimiter, SendJob, SendOutcome, SendQueue
from tests.bot.conftest import NIGHT, NOON, FakeBot, FakeSleep, query


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class ClockSleep(FakeSleep):
    """Fake sleep that moves the fake clock forward, like real waiting would."""

    def __init__(self, clock):
        super().__init__()
        self.clock = clock

    async def __call__(self, seconds):
        self.waits.append(seconds)
        self.clock.t += seconds


def _deliver(bot, **kwargs):
    clock = FakeClock()
    sleep = ClockSleep(clock)
    q = SendQueue(bot=bot, limiter=RateLimiter(clock=clock), sleep=sleep, **kwargs)
    outcome = asyncio.run(q.deliver(SendJob(chat_id="1", text="hi")))
    return outcome, sleep


# --- rate limiter -----------------------------------------------------------


def test_per_chat_limit_one_message_per_second():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    assert limiter.wait_time("a") == 0
    limiter.record("a")
    assert limiter.wait_time("a") == pytest.approx(1.0)
    assert limiter.wait_time("b") == 0  # other chats are not affected
    clock.t = 1.0
    assert limiter.wait_time("a") == 0


def test_global_limit_30_per_second():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    for i in range(30):
        limiter.record(f"chat{i}")
    assert limiter.wait_time("new_chat") == pytest.approx(1.0)
    clock.t = 1.0
    assert limiter.wait_time("new_chat") == 0


def test_chat_rate_limiter_10_per_minute():
    clock = FakeClock()
    limiter = ChatRateLimiter(max_messages=10, window_s=60, clock=clock)
    assert all(limiter.allow("U1") for _ in range(10))
    assert not limiter.allow("U1")
    assert limiter.allow("U2")
    clock.t = 60
    assert limiter.allow("U1")


# --- Telegram errors ----------------------------------------------------------


def test_success():
    bot = FakeBot()
    outcome, _ = _deliver(bot)
    assert outcome.status == "sent" and len(bot.sent) == 1


def test_forbidden_means_blocked_and_no_retry():
    bot = FakeBot([Forbidden("Forbidden: bot was blocked by the user")])
    outcome, _ = _deliver(bot)
    assert outcome.status == "blocked" and bot.calls == 1


def test_retry_after_waits_then_succeeds():
    bot = FakeBot([RetryAfter(5)])
    outcome, sleep = _deliver(bot)
    assert outcome.status == "sent" and bot.calls == 2
    assert 5 in sleep.waits


def test_retry_after_too_long_gives_up():
    bot = FakeBot([RetryAfter(600)])
    outcome, _ = _deliver(bot)
    assert outcome.status == "failed" and bot.calls == 1


def test_network_errors_retry_then_fail():
    bot = FakeBot([TimedOut(), NetworkError("down"), NetworkError("down")])
    outcome, sleep = _deliver(bot, max_attempts=3)
    assert outcome.status == "failed" and bot.calls == 3
    assert sleep.waits == [2, 4]  # backoff between tries


def test_network_error_then_success():
    bot = FakeBot([NetworkError("blip")])
    outcome, _ = _deliver(bot)
    assert outcome.status == "sent" and bot.calls == 2


def test_bad_request_is_not_retried():
    bot = FakeBot([BadRequest("Chat not found")])
    outcome, _ = _deliver(bot)
    assert outcome.status == "failed" and bot.calls == 1


def test_queue_thread_delivers():
    bot = FakeBot()
    q = SendQueue(bot=bot)
    assert q.submit("42", "hello").result(timeout=5).status == "sent"
    assert bot.sent[0]["chat_id"] == "42"


# --- send_offer / send_text ----------------------------------------------------


@pytest.fixture
def fake_delivery(monkeypatch):
    """Replace the real queue: record what would be sent and return a chosen outcome."""
    calls = []
    state = {"outcome": SendOutcome("sent")}

    def fake(chat_id, text, markup):
        calls.append({"chat_id": chat_id, "text": text, "markup": markup})
        return state["outcome"]

    monkeypatch.setattr(sender, "_deliver", fake)
    return calls, state


def _last_send(user_id):
    return query("SELECT * FROM offer_sends WHERE user_id = ? ORDER BY send_id DESC LIMIT 1", (user_id,))[0]


def test_send_offer_delivers_with_buttons(bot_db, fake_delivery):
    calls, _ = fake_delivery
    assert sender.send_offer("U00001", "10% off for you!", "OF0001", now=NOON)
    assert calls[0]["chat_id"] == "900000001"
    buttons = [b.callback_data for row in calls[0]["markup"].inline_keyboard for b in row]
    row = _last_send("U00001")
    assert row["status"] == "sent" and row["channel"] == "telegram" and row["trigger_type"] == "cart_abandon"
    assert buttons == [f"o:v:{row['send_id']}", f"o:a:{row['send_id']}", f"o:n:{row['send_id']}"]


def test_send_offer_blocked_when_opted_out(bot_db, fake_delivery):
    calls, _ = fake_delivery
    store.update_consent("U00001", opted_in=0)
    assert not sender.send_offer("U00001", "hi", "OF0001", now=NOON)
    assert calls == [] and _last_send("U00001")["status"] == "blocked"
    audit = query("SELECT * FROM audit_log WHERE action = 'send_offer' ORDER BY id DESC LIMIT 1")[0]
    assert audit["decision"] == "blocked" and audit["reason"] == "user is not opted in"


def test_send_offer_blocked_in_quiet_hours(bot_db, fake_delivery):
    calls, _ = fake_delivery
    assert not sender.send_offer("U00001", "hi", "OF0001", now=NIGHT)
    assert calls == [] and _last_send("U00001")["status"] == "blocked"


def test_send_offer_blocked_when_not_linked(bot_db, fake_delivery):
    calls, _ = fake_delivery
    assert not sender.send_offer("U00004", "hi", "OF0001", now=NOON)  # U00004 never pressed Start
    assert calls == []


def test_user_blocked_bot_marks_link_inactive_and_opts_out(bot_db, fake_delivery):
    _, state = fake_delivery
    state["outcome"] = SendOutcome("blocked", "user blocked the bot")
    assert not sender.send_offer("U00001", "hi", "OF0001", now=NOON)
    assert _last_send("U00001")["status"] == "failed"
    assert store.chat_for_user("U00001") is None
    assert not store.is_opted_in("U00001")


def test_send_text_with_quick_replies(bot_db, fake_delivery):
    calls, _ = fake_delivery
    assert sender.send_text("U00001", "Time to restock atta?", buttons=["Show more"], now=NOON)
    assert calls[0]["markup"].inline_keyboard[0][0].callback_data == "b:Show more"


def test_no_token_means_failed_not_crash(bot_db, monkeypatch):
    from common.config import settings

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
    assert not sender.send_offer("U00001", "hi", "OF0001", now=NOON)
    assert _last_send("U00001")["status"] == "failed"
