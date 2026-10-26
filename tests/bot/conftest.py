"""Bot test helpers: a temp DB with sample data, and fake Telegram objects.

No test here talks to the real Telegram — the fakes just record what the bot would send.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

# Daytime, inside every sample offer's validity window and outside quiet hours (22:00–08:00).
NOON = datetime(2026, 10, 1, 12, 0, 0)
NIGHT = datetime(2026, 10, 1, 23, 30, 0)


@pytest.fixture
def bot_db(sample_db, monkeypatch, use_sample_engine):
    """Point the whole app (bot, stubs, real reco, audit) at a temp copy of the sample data."""
    from common import stubs
    from common.config import settings

    monkeypatch.setattr(settings, "DB_PATH", sample_db)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_USERNAME", "test_shop_bot")
    stubs._table.cache_clear()  # stubs cache tables per process
    yield sample_db
    stubs._table.cache_clear()


def query(sql: str, params=()):
    from common.db import get_connection

    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


class FakeMessage:
    def __init__(self, text: str = ""):
        self.text = text
        self.replies: list[tuple[str, object]] = []

    async def reply_text(self, text, reply_markup=None):
        self.replies.append((text, reply_markup))


class FakeQuery:
    def __init__(self, data: str, message: FakeMessage):
        self.data = data
        self.message = message
        self.edited_markup = "untouched"
        self.edited_text = None

    async def answer(self):
        pass

    async def edit_message_reply_markup(self, reply_markup=None):
        self.edited_markup = reply_markup

    async def edit_message_text(self, text, reply_markup=None):
        self.edited_text = text
        self.edited_markup = reply_markup


def make_update(chat_id, text: str = "", callback_data: str | None = None):
    """A stand-in for telegram.Update with only the fields our handlers use."""
    message = FakeMessage(text)
    q = FakeQuery(callback_data, message) if callback_data is not None else None
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), effective_message=message,
                           callback_query=q)


def make_context(args=None):
    return SimpleNamespace(args=args or [], error=None)


class FakeBot:
    """Pretends to be telegram.Bot. `script` = list of exceptions to raise (None = success)."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.sent: list[dict] = []
        self.calls = 0

    async def send_message(self, chat_id, text, reply_markup=None):
        self.calls += 1
        outcome = self.script.pop(0) if self.script else None
        if outcome is not None:
            raise outcome
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})


class FakeSleep:
    def __init__(self):
        self.waits: list[float] = []

    async def __call__(self, seconds):
        self.waits.append(seconds)
