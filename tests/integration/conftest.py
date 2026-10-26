"""Integration test setup: the REAL modules wired together, with only the outside world faked.

Real: reco, offers (generator, eligibility, guardrails, the Offers API app itself), llm (mock
mode = no Claude calls), bot (linking, sender, queue, button handlers), pipeline.
Fake:  the HTTP hop to the Offers API (goes straight into the FastAPI app with TestClient)
       and Telegram (a FakeBot that records messages instead of sending them).

Every test runs on a fresh copy of the sample data (tests/conftest.py::_hermetic_defaults)
and uses the real clock, so the offers made by the generator are "live" for the bot's
Apply button too. Quiet hours are switched off per user unless a test sets them.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
import requests
from fastapi.testclient import TestClient

from common.config import settings
from common.db import get_connection
from tests.bot.conftest import FakeBot, make_context, make_update

TEST_KEY = "integration-test-key-123456"


class FakeRequests:
    """Stands in for the `requests` module inside offers.client / pipeline: sends the call
    to the Offers API FastAPI app in-process, or raises ConnectionError when `down`."""

    RequestException = requests.RequestException

    def __init__(self, app):
        self.client = TestClient(app)
        self.down = False
        self.calls: list[str] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(urlparse(url).path)
        if self.down:
            raise requests.ConnectionError("connection refused (fake)")
        return self.client.get(urlparse(url).path, params=params, headers=headers)


@pytest.fixture
def offers_api(monkeypatch):
    """The Offers API, 'running' in-process. Set `offers_api.down = True` to switch it off."""
    from offers import client
    from offers.api import app
    from pipeline import offer_pipeline

    monkeypatch.setattr(settings, "OFFERS_API_KEY", TEST_KEY)
    fake = FakeRequests(app)
    monkeypatch.setattr(client, "requests", fake)
    monkeypatch.setattr(offer_pipeline, "requests", fake)
    monkeypatch.setattr(client, "_sleep", lambda s: None)  # no real waiting between retries
    return fake


@pytest.fixture
def telegram(monkeypatch):
    """Real bot send queue + a FakeBot instead of api.telegram.org."""
    from bot import sender

    fake = FakeBot()
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123456:fake-token-for-tests")
    monkeypatch.setattr(sender, "_queue", sender.SendQueue(bot=fake))
    yield fake
    sender._queue = None


@pytest.fixture
def llm_mock():
    """Claude in mock mode (no network), even if a real key is in .env."""
    from llm.client import LLMClient, set_client

    set_client(LLMClient(api_key=""))
    yield
    set_client(None)


@pytest.fixture
def system(offers_api, telegram, llm_mock):
    """Everything up: Offers API, Telegram (fake), Claude (mock)."""
    return SimpleNamespace(offers_api=offers_api, telegram=telegram)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(coro):
    return asyncio.run(coro)


def query(sql: str, params=()) -> list[dict]:
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def execute(sql: str, params=()) -> None:
    with get_connection() as conn:
        conn.execute(sql, params)


def link_telegram(user_id: str, chat_id: int) -> None:
    """What a real user does: website gives a deep link -> they press Start in Telegram."""
    from bot import handlers
    from bot.linking import create_link_token

    token = create_link_token(user_id).split("?start=")[1]
    update = make_update(chat_id, text=f"/start {token}")
    run(handlers.cmd_start(update, make_context(args=[token])))
    assert "linked" in update.effective_message.replies[0][0]
    no_quiet_hours(user_id)


def no_quiet_hours(user_id: str) -> None:
    # quiet_start == quiet_end means "no quiet window" -> tests don't depend on the time of day.
    execute("UPDATE user_consent SET quiet_start = '00:00', quiet_end = '00:00' WHERE user_id = ?", (user_id,))


def add_event(user_id: str, product_id: str, event_type: str, when, source: str = "product_page") -> None:
    n = query("SELECT COUNT(*) AS n FROM events")[0]["n"]
    execute("INSERT INTO events (event_id, user_id, product_id, session_id, event_type, timestamp, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"E9{n:08d}", user_id, product_id, "S9999999", event_type, when.isoformat(timespec="seconds"), source))


def press_button(chat_id: int, callback_data: str) -> list[str]:
    from bot import handlers

    update = make_update(chat_id, callback_data=callback_data)
    run(handlers.on_button(update, make_context()))
    return [t for t, _ in update.effective_message.replies]


def pipeline_audit(user_id: str) -> list[dict]:
    return query("SELECT action, decision, reason FROM audit_log WHERE component = 'pipeline' AND user_id = ? "
                 "ORDER BY id", (user_id,))
