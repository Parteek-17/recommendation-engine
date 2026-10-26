"""Account linking: deep-link tokens are single-use and expire after 24 hours."""

from datetime import timedelta

import pytest

from bot import store
from bot.linking import create_link_token, link_chat, token_issued_at
from tests.bot.conftest import NOON, query


def _token(link: str) -> str:
    return link.split("?start=")[1]


def test_create_link_token_returns_deep_link(bot_db):
    link = create_link_token("U00010", now=NOON)
    assert link.startswith("https://t.me/test_shop_bot?start=")
    token = _token(link)
    assert len(token) <= 64 and token_issued_at(token) == NOON
    row = query("SELECT * FROM telegram_links WHERE user_id = 'U00010'")[0]
    assert row["link_token"] == token and row["active"] == 0 and row["chat_id"] is None


def test_unknown_user_is_rejected(bot_db):
    with pytest.raises(ValueError):
        create_link_token("U99999")


def test_start_with_token_links_and_opts_in(bot_db):
    store.update_consent("U00010", opted_in=0)
    token = _token(create_link_token("U00010", now=NOON))
    result = link_chat(token, 555, now=NOON + timedelta(minutes=5))
    assert result.ok and result.reason == "linked" and result.user_id == "U00010"
    assert store.user_for_chat(555) == "U00010"
    assert store.is_opted_in("U00010")


def test_token_is_single_use(bot_db):
    token = _token(create_link_token("U00010", now=NOON))
    assert link_chat(token, 555, now=NOON + timedelta(minutes=1)).reason == "linked"
    # Same chat pressing Start again is harmless...
    assert link_chat(token, 555, now=NOON + timedelta(minutes=2)).reason == "already_linked"
    # ...but someone else can't reuse the token.
    result = link_chat(token, 777, now=NOON + timedelta(minutes=3))
    assert not result.ok and result.reason == "used"
    assert store.user_for_chat(777) is None


def test_token_expires_after_24_hours(bot_db):
    token = _token(create_link_token("U00010", now=NOON))
    result = link_chat(token, 555, now=NOON + timedelta(hours=25))
    assert not result.ok and result.reason == "expired"


def test_invalid_token(bot_db):
    assert link_chat("nope", 555, now=NOON).reason == "invalid"


def test_new_token_relinks_to_new_chat(bot_db):
    first = _token(create_link_token("U00010", now=NOON))
    link_chat(first, 555, now=NOON + timedelta(minutes=1))
    second = _token(create_link_token("U00010", now=NOON + timedelta(hours=1)))
    assert store.user_for_chat(555) == "U00010"  # old link keeps working until the new one is used
    assert link_chat(second, 666, now=NOON + timedelta(hours=1, minutes=1)).reason == "linked"
    assert store.user_for_chat(666) == "U00010" and store.user_for_chat(555) is None


def test_old_style_sample_token_works(bot_db):
    # Sample data has a pending token without a time part for U00004.
    assert link_chat("2c886831429e", 444, now=NOON).reason == "linked"
    assert store.user_for_chat(444) == "U00004"
