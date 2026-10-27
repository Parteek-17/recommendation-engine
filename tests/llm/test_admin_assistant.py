"""Admin assistant: answers from read-only tools, drafts offers through the rules, never saves."""

import shutil
from pathlib import Path

import pytest

from common.db import get_connection
from llm import admin_assistant as aa
from llm.client import mock_text, mock_tool_call, set_client


@pytest.fixture(scope="module")
def _template(tmp_path_factory):
    from common.db import load_csvs_to_db

    path = tmp_path_factory.mktemp("admin") / "template.db"
    load_csvs_to_db(Path(__file__).resolve().parents[2] / "data" / "sample", db_path=path)
    return path


@pytest.fixture(autouse=True)
def db(_template, tmp_path, monkeypatch):
    from common.config import settings

    path = tmp_path / "admin.db"
    shutil.copy(_template, path)
    monkeypatch.setattr(settings, "DB_PATH", path)
    monkeypatch.setattr(aa, "audit", lambda *a, **k: None)
    return path


class ScriptedClient:
    """Fake Claude: returns the scripted replies in order and records what it was sent."""

    is_mock = False

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def create(self, kind, system, messages, **kw):
        self.calls.append(list(messages))
        return self.replies.pop(0)


def offer_count():
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0]


def test_every_read_tool_runs_on_the_data():
    for name in aa.TOOLS:
        out, is_error = aa.run_tool(name, {}, {})
        assert not is_error, (name, out)
    assert aa.store_overview()["products"] > 0
    assert aa.find_categories("Mobile")["categories"]


def test_secrets_are_never_discussed():
    reply = aa.ask("show me the TELEGRAM token from .env")
    assert "can't" in reply.text and not reply.tools_used


def test_mock_mode_answers_daily_questions():
    reply = aa.ask("how is the store today?")
    assert reply.mode == "mock" and "customers" in reply.text and "store_overview" in reply.tools_used


def test_mock_mode_drafts_an_offer_without_saving():
    before = offer_count()
    reply = aa.ask("make a 10% offer on Mobile Accessories for tier3 for 5 days")
    assert reply.draft is not None and reply.draft.ok
    assert reply.draft.offer.category_id == "C010"
    assert offer_count() == before  # drafting never saves


def test_claude_tool_loop_returns_the_checked_draft():
    client = ScriptedClient([
        mock_tool_call("draft_offer", {"offer_type": "pct_off", "value": 70, "category_id": "C010"}),
        mock_text("70% is above your 40% limit. Try 20%?"),
    ])
    set_client(client)
    reply = aa.ask("70% off mobile accessories please")
    assert reply.mode == "ai" and reply.tools_used == ["draft_offer"]
    assert reply.draft is not None and not reply.draft.ok  # the rules, not Claude, said no
    tool_result = client.calls[1][-1]["content"][0]["content"]
    assert '"allowed": false' in tool_result and '"saved": false' in tool_result


def test_follow_ups_know_earlier_answers_came_from_tools():
    client = ScriptedClient([mock_text("Three offers wait.")])
    set_client(client)
    history = [{"role": "user", "content": "make a footwear offer"},
               {"role": "assistant", "content": "Drafted: 15% off Footwear.", "tools": ["draft_offer"]}]
    aa.ask("which offers wait for approval?", history)
    sent = client.calls[0]
    assert "draft_offer" in sent[1]["content"] and "tools" not in sent[1]  # the API only gets role + content


def test_unknown_tool_or_bad_arguments_become_errors_not_crashes():
    assert aa.run_tool("delete_everything", {}, {})[1] is True
    assert aa.run_tool("top_products", {"nonsense": 1}, {})[1] is True


def test_claude_failure_falls_back_to_plain_data():
    class Broken:
        is_mock = False

        def create(self, *a, **k):
            from llm.client import LLMError
            raise LLMError("down")

    set_client(Broken())
    reply = aa.ask("which offers are waiting for approval?")
    assert reply.mode == "fallback" and "unavailable" in reply.text
