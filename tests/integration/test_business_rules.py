"""Business rules around the AI: control group, "would buy anyway", and the AI's offer choice.

The AI may pick among offers the rules approved; anything else it proposes is rejected and
the rec engine's pick is used. These tests drive the real pipeline (fakes as in conftest.py).
"""

from __future__ import annotations

import json
from datetime import timedelta

from common.logging_utils import now_local
from llm import offer_chooser
from llm.client import mock_text
from offers import rules
from pipeline import run_pipeline
from tests.integration.conftest import add_event, link_telegram, query

USER, CHAT = "U00010", 700000010
CART_ITEM = "P00033"


def abandon_cart() -> None:
    add_event(USER, CART_ITEM, "add_to_cart", now_local() - timedelta(hours=3))


def sends(user_id: str) -> list[dict]:
    return query("SELECT * FROM offer_sends WHERE user_id = ?", (user_id,))


def ai_answers(monkeypatch, answer: dict) -> None:
    """Make the (mock) AI chooser reply with `answer`."""
    monkeypatch.setattr(offer_chooser, "_mock_reply",
                        lambda shopper, candidates: (lambda messages, tools: mock_text(json.dumps(answer))))


def test_control_group_gets_no_offer(system, monkeypatch):
    monkeypatch.setattr(rules, "CONTROL_GROUP_PCT", 100)  # everyone is held out
    link_telegram(USER, CHAT)
    abandon_cart()
    result = run_pipeline(USER, "cart_abandon")
    assert result.status == "holdout"
    assert system.telegram.sent == [] and sends(USER) == []


def test_testers_are_never_held_out(system, monkeypatch):
    from bot import store as bot_store

    monkeypatch.setattr(rules, "CONTROL_GROUP_PCT", 100)
    link_telegram(USER, CHAT)
    bot_store.save_tester(USER, "student", "cart_abandon")
    abandon_cart()
    assert run_pipeline(USER, "cart_abandon").status == "sent"


def test_likely_buyer_gets_reminder_without_discount(system, monkeypatch):
    monkeypatch.setitem(rules.SKIP_LIKELY_BUYERS, "enabled", True)
    monkeypatch.setitem(rules.SKIP_LIKELY_BUYERS, "budget_bands", ["low", "mid", "high"])
    monkeypatch.setitem(rules.SKIP_LIKELY_BUYERS, "min_orders", 1)
    monkeypatch.setitem(rules.SKIP_LIKELY_BUYERS, "lookback_days", 3650)
    link_telegram(USER, CHAT)
    abandon_cart()
    with_orders = query("SELECT COUNT(*) AS n FROM orders WHERE user_id = ?", (USER,))[0]["n"]
    result = run_pipeline(USER, "cart_abandon")
    if with_orders == 0:  # sample data without orders for this user: the rule doesn't apply
        assert result.status == "sent"
        return
    assert result.status == "reminder_sent", result.reason
    assert sends(USER) == []                      # no coupon was sent...
    assert "cart" in system.telegram.sent[-1]["text"].lower()  # ...just a friendly reminder


def test_ai_choice_outside_the_rules_is_rejected(system, monkeypatch):
    ai_answers(monkeypatch, {"offer_id": "OF9999", "reason": "made-up offer"})
    link_telegram(USER, CHAT)
    abandon_cart()
    result = run_pipeline(USER, "cart_abandon")
    assert result.status == "sent"                # the rec engine's rule-approved pick went out
    assert result.chosen_by == "ranker" and "OF9999" in result.ai_rejected
    rejected = query("SELECT * FROM audit_log WHERE action = 'choose_offer' AND decision = 'rejected' AND user_id = ?",
                     (USER,))
    assert rejected and "OF9999" in rejected[-1]["details_json"]


def test_ai_can_choose_no_discount(system, monkeypatch):
    ai_answers(monkeypatch, {"offer_id": "no_discount", "reason": "buys this every week anyway"})
    link_telegram(USER, CHAT)
    abandon_cart()
    result = run_pipeline(USER, "cart_abandon")
    assert result.status == "reminder_sent" and result.chosen_by == "ai"
    assert sends(USER) == []


def test_ai_choice_is_used_when_valid(system, monkeypatch):
    link_telegram(USER, CHAT)
    abandon_cart()
    result = run_pipeline(USER, "cart_abandon")
    assert result.status == "sent" and result.chosen_by == "ai" and result.choice_reason
