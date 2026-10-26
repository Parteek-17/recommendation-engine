"""Classmate demo flow: /start without a link -> pick a shopper -> the real pipeline sends their offer."""

from __future__ import annotations

from bot import handlers, personas
from bot import store as bot_store
from tests.bot.conftest import make_context, make_update
from tests.integration.conftest import press_button, query, run


def test_start_without_link_offers_persona_buttons(system):
    update = make_update(700000101, text="/start")
    run(handlers.cmd_start(update, make_context()))
    text, markup = update.effective_message.replies[0]
    assert "pick a shopper" in text.lower()
    labels = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert labels == [f"t:{p.key}" for p in personas.PERSONAS]


def test_picking_a_persona_links_and_runs_the_pipeline(system):
    chat = 700000102
    replies = press_button(chat, "t:student")
    user_id = bot_store.user_for_chat(chat)
    assert user_id is not None and bot_store.is_opted_in(user_id)
    assert bot_store.tester_persona(user_id)["persona"] == "student"
    assert any("Behind the scenes" in r for r in replies)
    runs = query("SELECT status FROM pipeline_runs WHERE user_id = ?", (user_id,))
    assert runs, "the pipeline ran for the new tester"


def test_two_testers_never_share_a_shopper(system):
    press_button(700000103, "t:wishlist")
    press_button(700000104, "t:wishlist")
    a, b = bot_store.user_for_chat(700000103), bot_store.user_for_chat(700000104)
    assert a and b and a != b


def test_switch_moves_the_chat_to_another_shopper(system):
    chat = 700000105
    press_button(chat, "t:grocer")
    first = bot_store.user_for_chat(chat)
    press_button(chat, "t:lapsed")
    second = bot_store.user_for_chat(chat)
    assert second and second != first
    assert {t["persona"] for t in bot_store.list_testers()} >= {"grocer", "lapsed"}
