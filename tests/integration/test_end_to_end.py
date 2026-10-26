"""End-to-end: trigger -> eligibility -> Offers API -> rank -> guardrails -> LLM -> revalidate -> Telegram.

Only Telegram and the HTTP hop are faked (see conftest.py); every module is the real one.
"""

from __future__ import annotations

import json
from datetime import timedelta

from common.logging_utils import now_local
from pipeline import detect_triggers, run_offer_pipeline, run_pipeline
from tests.integration.conftest import (
    add_event, execute, link_telegram, pipeline_audit, press_button, query,
)

NEW_USER, NEW_CHAT = "U00010", 700000010   # not linked, not opted in in the sample data
CART_ITEM = "P00033"                        # Adidas Sports Socks: in stock, not age-restricted

APPROVED_ORDER = ["step_eligibility", "step_fetch", "step_rank", "step_guardrails", "step_choose",
                  "step_write", "step_revalidate", "step_send"]


def abandon_cart(user_id: str = NEW_USER, hours_ago: float = 3) -> None:
    add_event(user_id, CART_ITEM, "add_to_cart", now_local() - timedelta(hours=hours_ago))


def sends(user_id: str) -> list[dict]:
    return query("SELECT * FROM offer_sends WHERE user_id = ? ORDER BY send_id", (user_id,))


# ---------------------------------------------------------------------------
# The happy path, exactly like the demo
# ---------------------------------------------------------------------------


def test_new_user_links_abandons_cart_gets_offer_and_redeems(system):
    # 1. A new shopper links Telegram (deep link -> Start = opt-in).
    link_telegram(NEW_USER, NEW_CHAT)
    assert query("SELECT opted_in FROM user_consent WHERE user_id = ?", (NEW_USER,))[0]["opted_in"] == 1

    # 2. They add socks to the cart and leave (3 hours ago).
    abandon_cart()
    assert (NEW_USER, "cart_abandon") in detect_triggers(now_local())

    # 3. The pipeline runs: an offer arrives in Telegram.
    result = run_pipeline(NEW_USER, "cart_abandon")
    assert result.status == "sent", result.reason
    assert [m["chat_id"] for m in system.telegram.sent] == [str(NEW_CHAT)]
    message = system.telegram.sent[0]["text"]
    offer = query("SELECT * FROM offers WHERE offer_id = ?", (result.offer_id,))[0]
    assert offer["code"] in message                       # the code comes from the DB...
    assert offer["segment"] == f"user:{NEW_USER}"          # ...and is personal
    assert offer["product_id"] == CART_ITEM
    assert system.offers_api.calls == ["/offers/live"]     # offers came through the Offers API

    # Every step ran, in the approved order, and was audited.
    steps = [a["action"] for a in pipeline_audit(NEW_USER) if a["action"].startswith("step_")]
    assert [s for s in steps if s != "step_prepare"] == APPROVED_ORDER

    # 4. The bot wrote exactly one offer_sends row (the pipeline doesn't write its own).
    [send] = sends(NEW_USER)
    assert send["status"] == "sent" and send["offer_id"] == result.offer_id and send["message"] == message

    # 5. The user taps "Apply offer" -> redeemed, and it lands in events as a telegram add_to_cart.
    buttons = system.telegram.sent[0]["reply_markup"].inline_keyboard
    apply = next(b.callback_data for row in buttons for b in row if b.callback_data.startswith("o:a:"))
    replies = press_button(NEW_CHAT, apply)
    assert offer["code"] in replies[0]
    assert sends(NEW_USER)[0]["status"] == "redeemed"
    assert query("SELECT COUNT(*) AS n FROM events WHERE user_id = ? AND source = 'telegram' "
                 "AND event_type = 'add_to_cart'", (NEW_USER,))[0]["n"] == 1


def test_second_offer_same_day_is_blocked_by_daily_cap(system):
    link_telegram(NEW_USER, NEW_CHAT)
    abandon_cart()
    assert run_offer_pipeline(NEW_USER, "cart_abandon") == "sent"
    assert run_offer_pipeline(NEW_USER, "festival") == "blocked"
    assert "daily cap" in pipeline_audit(NEW_USER)[-1]["reason"]
    assert len(system.telegram.sent) == 1


# ---------------------------------------------------------------------------
# Safety: who must NOT get anything
# ---------------------------------------------------------------------------


def test_opted_out_user_gets_nothing(system):
    from bot import handlers
    from tests.bot.conftest import make_context, make_update
    from tests.integration.conftest import run

    link_telegram(NEW_USER, NEW_CHAT)
    run(handlers.cmd_stop(make_update(NEW_CHAT, text="/stop"), make_context()))
    abandon_cart()

    assert run_offer_pipeline(NEW_USER, "cart_abandon") == "blocked"
    assert system.telegram.sent == [] and sends(NEW_USER) == []
    assert query("SELECT COUNT(*) AS n FROM offers WHERE segment = ?", (f"user:{NEW_USER}",))[0]["n"] == 0  # no coupon made
    assert "not opted in" in pipeline_audit(NEW_USER)[-1]["reason"]


def test_quiet_hours_respected(system):
    link_telegram(NEW_USER, NEW_CHAT)
    abandon_cart()
    now = now_local()
    start, end = (now - timedelta(hours=1)).strftime("%H:%M"), (now + timedelta(hours=1)).strftime("%H:%M")
    execute("UPDATE user_consent SET quiet_start = ?, quiet_end = ? WHERE user_id = ?", (start, end, NEW_USER))

    assert run_offer_pipeline(NEW_USER, "cart_abandon") == "blocked"
    assert system.telegram.sent == []
    assert "quiet hours" in pipeline_audit(NEW_USER)[-1]["reason"]


def test_offers_api_down_means_no_send_and_an_audit_entry(system):
    link_telegram(NEW_USER, NEW_CHAT)
    abandon_cart()
    system.offers_api.down = True

    assert run_offer_pipeline(NEW_USER, "cart_abandon") == "offers_api_down"
    assert system.telegram.sent == [] and sends(NEW_USER) == []
    fetch_fail = query("SELECT * FROM audit_log WHERE component = 'offers' AND action = 'fetch_live_offers' "
                       "AND decision = 'failed' AND user_id = ?", (NEW_USER,))
    assert fetch_fail and "unavailable" in fetch_fail[0]["reason"]
    assert pipeline_audit(NEW_USER)[-2:][0]["action"] == "step_fetch"
    # The API key never ends up in the audit log.
    assert not query("SELECT id FROM audit_log WHERE details_json LIKE ? OR reason LIKE ?",
                     ("%integration-test-key%", "%integration-test-key%"))


def test_llm_down_sends_template_message(system):
    from llm.client import LLMClient, LLMError, set_client
    from llm.templates import fallback_offer_message
    from offers.store import get_offer

    class DownClient(LLMClient):
        """Claude 'with a key' whose every request fails (network down / outage)."""

        def __init__(self):
            super().__init__(api_key="")
            self._sdk = object()  # not mock mode

        def create(self, *args, **kwargs):
            raise LLMError("Could not reach Claude (network/timeout)")

    set_client(DownClient())
    link_telegram(NEW_USER, NEW_CHAT)
    abandon_cart()

    result = run_pipeline(NEW_USER, "cart_abandon")
    assert result.status == "sent"
    assert system.telegram.sent[0]["text"] == fallback_offer_message(get_offer(result.offer_id), "cart_abandon")
    write = [a for a in pipeline_audit(NEW_USER) if a["action"] == "step_write"]
    assert write[0]["decision"] == "fallback"
    assert query("SELECT * FROM audit_log WHERE component = 'llm' AND action = 'write_offer_message' "
                 "AND decision = 'fallback'")


def test_guardrails_stop_unsafe_offers(system):
    """A minor with an 18+ item in the cart: no coupon is ever made for that item, and the
    over-limit 50% offer (MEGA50, sample data) is removed by the guardrails. A safe store-wide
    offer may still be sent (checkout separately refuses 18+ items for minors)."""
    minor = "U00007"
    link_telegram(minor, 700000007)
    restricted = query("SELECT product_id FROM products WHERE age_restricted = 1 AND stock_qty > 0 LIMIT 1")[0]["product_id"]
    add_event(minor, restricted, "add_to_cart", now_local() - timedelta(hours=3))

    result = run_pipeline(minor, "cart_abandon")
    assert not query("SELECT offer_id FROM offers WHERE product_id = ? OR bundle_product_id = ?",
                     (restricted, restricted))
    if result.status == "sent":
        offer = query("SELECT * FROM offers WHERE offer_id = ?", (result.offer_id,))[0]
        assert offer["product_id"] in (None, "") or offer["product_id"] != restricted
        assert float(offer["value"]) <= 40
    blocked = query("SELECT details_json, reason FROM audit_log WHERE action = 'offer_guardrail' "
                    "AND decision = 'blocked' AND user_id = ?", (minor,))
    assert any("OF0017" in b["details_json"] for b in blocked)  # MEGA50: 50% > MAX_DISCOUNT_PCT


def test_dry_run_shows_message_but_sends_nothing(system):
    link_telegram(NEW_USER, NEW_CHAT)
    abandon_cart()
    result = run_pipeline(NEW_USER, "cart_abandon", dry_run=True)
    assert result.status == "dry_run" and result.message
    assert system.telegram.sent == [] and sends(NEW_USER) == []


def test_crash_in_a_module_is_contained(system, monkeypatch):
    from pipeline import offer_pipeline

    def broken(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(offer_pipeline, "rank_offers_for_user", broken)
    link_telegram(NEW_USER, NEW_CHAT)
    abandon_cart()
    assert run_offer_pipeline(NEW_USER, "cart_abandon") == "error"
    assert system.telegram.sent == []
    last = query("SELECT * FROM audit_log WHERE action = 'run_offer_pipeline' ORDER BY id DESC LIMIT 1")[0]
    assert last["decision"] == "error" and "boom" in last["reason"]
    assert json.loads(last["details_json"])["trigger_type"] == "cart_abandon"
