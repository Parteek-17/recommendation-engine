"""Sending API used by the rest of the system: `send_offer` and `send_text`.

Who calls this? The offer pipeline (Phase 7) — usually from the scheduler process,
not from inside the running bot. So sending must work on its own, without the bot's
polling loop.

How it works:
    send_offer(...)                       (any thread, normal "blocking" function)
        │ 1. final checks: linked? still opted in? not quiet hours?
        │ 2. put a job on the queue and wait for the answer
        ▼
    SendQueue worker thread               (ONE thread, its own asyncio loop + telegram.Bot)
        │ waits as long as the RateLimiter says (≈1 msg/s per chat, ≤30 msg/s overall)
        │ calls Telegram; on errors:
        │   Forbidden  (user blocked the bot)  -> give up, report "blocked"
        │   RetryAfter (Telegram: slow down)   -> wait the seconds Telegram asks, try again
        │   network / timeout                  -> wait a bit longer each time, retry, then "failed"
        ▼
    back in send_offer: write offer_sends.status + audit_log, return True/False

One queue for the whole process means every message, from every caller, respects
the same rate limits.
"""

from __future__ import annotations

import asyncio
import threading
import time
import warnings
from collections import defaultdict, deque
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from telegram import Bot, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError

from bot import store
from bot.keyboards import clip, offer_keyboard, quick_reply_keyboard
from common.config import settings
from common.logging_utils import audit, get_logger, now_local

log = get_logger(__name__)

SEND_TIMEOUT_S = 120  # how long send_offer waits for the queue before giving up


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class RateLimiter:
    """Telegram's limits: about 1 message per second to the same chat, 30 per second overall.

    `clock` is injectable so tests can fake time instead of really waiting.
    """

    def __init__(self, per_chat_interval: float = 1.0, global_per_second: int = 30, clock=time.monotonic):
        self.per_chat_interval = per_chat_interval
        self.global_per_second = global_per_second
        self.clock = clock
        self._last_per_chat: dict[str, float] = {}
        self._recent: deque[float] = deque()  # send times in the last second (all chats)

    def wait_time(self, chat_id: str) -> float:
        """Seconds to wait before we may send to `chat_id` (0 = go now)."""
        now = self.clock()
        while self._recent and now - self._recent[0] >= 1.0:
            self._recent.popleft()
        wait_global = 0.0
        if len(self._recent) >= self.global_per_second:
            wait_global = 1.0 - (now - self._recent[0])
        last = self._last_per_chat.get(chat_id)
        wait_chat = 0.0 if last is None else self.per_chat_interval - (now - last)
        return max(0.0, wait_global, wait_chat)

    def record(self, chat_id: str) -> None:
        now = self.clock()
        self._last_per_chat[chat_id] = now
        self._recent.append(now)


class ChatRateLimiter:
    """Limits how many chat messages one user may send us (protects the LLM budget)."""

    def __init__(self, max_messages: int = 10, window_s: float = 60.0, clock=time.monotonic):
        self.max_messages = max_messages
        self.window_s = window_s
        self.clock = clock
        self._history: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self.clock()
        history = self._history[key]
        while history and now - history[0] >= self.window_s:
            history.popleft()
        if len(history) >= self.max_messages:
            return False
        history.append(now)
        return True


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


@dataclass
class SendOutcome:
    status: str  # "sent" | "blocked" (user blocked the bot) | "failed"
    reason: str = ""


@dataclass
class SendJob:
    chat_id: str
    text: str
    reply_markup: InlineKeyboardMarkup | None = None
    future: Future = field(default_factory=Future)


def _retry_after_seconds(exc: RetryAfter) -> float:
    """RetryAfter.retry_after is an int in PTB 21 and becomes a timedelta in later versions."""
    with warnings.catch_warnings():  # PTB 22 warns about that upcoming change on every read
        warnings.simplefilter("ignore")
        value = exc.retry_after
    return value.total_seconds() if isinstance(value, timedelta) else float(value)


class SendQueue:
    """A single worker that sends messages one by one, respecting rate limits and retrying."""

    def __init__(self, bot=None, limiter: RateLimiter | None = None, max_attempts: int = 3,
                 max_retry_after_s: float = 60.0, sleep=asyncio.sleep):
        self.bot = bot
        self.limiter = limiter or RateLimiter()
        self.max_attempts = max_attempts
        self.max_retry_after_s = max_retry_after_s
        self.sleep = sleep
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._started = threading.Event()
        self._start_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    async def deliver(self, job: SendJob) -> SendOutcome:
        """Send one message with all the error handling. Never raises."""
        attempt = 0
        flood_waits = 0
        while attempt < self.max_attempts:
            wait = self.limiter.wait_time(job.chat_id)
            if wait > 0:
                await self.sleep(wait)
            self.limiter.record(job.chat_id)
            try:
                await self.bot.send_message(chat_id=job.chat_id, text=job.text, reply_markup=job.reply_markup)
                return SendOutcome("sent")
            except Forbidden:
                return SendOutcome("blocked", "user blocked the bot")
            except RetryAfter as exc:
                # Telegram tells us exactly how long to wait. Doesn't count as a failed attempt,
                # but we stop if it keeps happening.
                flood_waits += 1
                wait_s = _retry_after_seconds(exc)
                if flood_waits > 3 or wait_s > self.max_retry_after_s:
                    return SendOutcome("failed", f"rate limited by Telegram (retry_after={wait_s:g}s)")
                log.warning("Telegram asked us to wait %.0fs", wait_s)
                await self.sleep(wait_s)
            except BadRequest as exc:
                # A wrong request (e.g. chat not found) won't fix itself: don't retry.
                # (BadRequest is a kind of NetworkError in PTB, so it must be caught first.)
                return SendOutcome("failed", f"bad request: {exc.message}")
            except NetworkError as exc:  # includes TimedOut
                attempt += 1
                log.warning("Network problem sending to Telegram (try %d/%d): %s", attempt, self.max_attempts, exc)
                if attempt < self.max_attempts:
                    await self.sleep(2 ** attempt)  # 2s, 4s, ...
            except TelegramError as exc:
                return SendOutcome("failed", f"telegram error: {exc.message}")
            except Exception as exc:  # never let one bad message kill the worker
                log.exception("Unexpected error while sending")
                return SendOutcome("failed", f"unexpected error: {type(exc).__name__}")
        return SendOutcome("failed", "network errors, gave up after retries")

    # --- background thread --------------------------------------------------

    def start(self) -> None:
        """Start the worker thread (once, even if two threads call this at the same time)."""
        with self._start_lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name="telegram-send-queue", daemon=True)
            self._thread.start()
        self._started.wait(timeout=10)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._queue = asyncio.Queue()
        self._started.set()
        self._loop.run_until_complete(self._worker())

    async def _worker(self) -> None:
        if hasattr(self.bot, "initialize"):
            await self.bot.initialize()
        while True:
            job: SendJob = await self._queue.get()
            outcome = await self.deliver(job)
            if not job.future.done():
                job.future.set_result(outcome)

    def submit(self, chat_id: str, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> Future:
        """Add a message to the queue. Returns a Future; `.result()` gives the SendOutcome."""
        self.start()
        job = SendJob(chat_id=str(chat_id), text=clip(text), reply_markup=reply_markup)
        self._loop.call_soon_threadsafe(self._queue.put_nowait, job)
        return job.future


_queue: SendQueue | None = None
_queue_lock = threading.Lock()


def get_queue() -> SendQueue:
    """The one shared queue of this process (created on first use)."""
    global _queue
    with _queue_lock:
        if _queue is None:
            # require() gives a friendly error; the token itself is never logged.
            _queue = SendQueue(bot=Bot(token=settings.require("TELEGRAM_BOT_TOKEN")))
        return _queue


# ---------------------------------------------------------------------------
# Public API (Module Contract)
# ---------------------------------------------------------------------------


def pre_send_check(user_id: str, now: datetime | None = None) -> tuple[str | None, str]:
    """Last check before ANY proactive message. Returns (chat_id, "ok") or (None, why_not)."""
    chat_id = store.chat_for_user(user_id)
    if chat_id is None:
        return None, "user has no active Telegram link"
    if not store.is_opted_in(user_id):
        return None, "user is not opted in"
    if store.is_quiet_time(user_id, now):
        return None, "quiet hours"
    return chat_id, "ok"


def _deliver(chat_id: str, text: str, markup) -> SendOutcome:
    if not settings.has("TELEGRAM_BOT_TOKEN"):
        return SendOutcome("failed", "TELEGRAM_BOT_TOKEN is not set")
    try:
        return get_queue().submit(chat_id, text, markup).result(timeout=SEND_TIMEOUT_S)
    except Exception as exc:  # timeout, queue failure: report, never crash the caller
        log.error("Send queue problem: %s", type(exc).__name__)
        return SendOutcome("failed", f"send queue error: {type(exc).__name__}")


def _handle_blocked(user_id: str) -> None:
    """The user blocked the bot: stop everything for them until they press Start again."""
    store.deactivate_link(user_id)
    store.update_consent(user_id, opted_in=0)


def send_offer(user_id: str, message: str, offer_id: str, now: datetime | None = None) -> bool:
    """Send an offer message with View / Apply / Not interested buttons.

    Writes the `offer_sends` row (queued -> sent / blocked / failed) and an audit entry.
    Returns True only if Telegram accepted the message.
    """
    offer = store.get_offer(offer_id)
    trigger = offer.get("trigger_type") if offer else None
    send_id = store.create_offer_send(user_id, offer_id, message, trigger, now)
    details = {"offer_id": offer_id, "send_id": send_id}

    if offer is None:
        store.set_send_status(send_id, "blocked", now)
        audit("bot", user_id, "send_offer", "blocked", "offer not found", details)
        return False
    chat_id, why = pre_send_check(user_id, now)
    if chat_id is None:
        store.set_send_status(send_id, "blocked", now)
        audit("bot", user_id, "send_offer", "blocked", why, details)
        return False

    outcome = _deliver(chat_id, message, offer_keyboard(send_id))
    if outcome.status == "sent":
        store.set_send_status(send_id, "sent", now)
        audit("bot", user_id, "send_offer", "sent", "delivered to Telegram", details)
        return True
    if outcome.status == "blocked":
        _handle_blocked(user_id)
    store.set_send_status(send_id, "failed", now)
    audit("bot", user_id, "send_offer", "failed", outcome.reason, details)
    return False


def send_text(user_id: str, text: str, buttons: list[str] | None = None, now: datetime | None = None) -> bool:
    """Send a plain text message (e.g. a repurchase reminder). `buttons` are quick replies:
    tapping one is the same as the user typing that text to the shopping assistant."""
    chat_id, why = pre_send_check(user_id, now)
    if chat_id is None:
        audit("bot", user_id, "send_text", "blocked", why)
        return False
    outcome = _deliver(chat_id, text, quick_reply_keyboard(buttons or []))
    if outcome.status == "blocked":
        _handle_blocked(user_id)
    decision = "sent" if outcome.status == "sent" else "failed"
    audit("bot", user_id, "send_text", decision, outcome.reason or "delivered to Telegram")
    return outcome.status == "sent"
