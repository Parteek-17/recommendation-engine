"""Offer message writer: placeholders filled from DB, invalid drafts fall back to templates."""

import re

import pytest

from common import stubs
from common.schemas import UserContext
from llm import templates
from llm.client import LLMClient, LLMError, mock_text, set_client
from llm.offer_writer import MAX_MESSAGE_CHARS, validate_offer_text, write_offer_message


@pytest.fixture
def offers():
    return {o.offer_id: o for o in stubs._all_offers()}


@pytest.fixture
def ctx():
    return UserContext(user_id="U00001", budget_band="mid", top_categories=["C009", "C010"])


class FakeClient(LLMClient):
    """Pretends to be a real client and returns a fixed draft (or raises)."""

    def __init__(self, draft=None, error=False):
        super().__init__(api_key="")
        self.draft, self.error, self.calls = draft, error, []

    @property
    def is_mock(self):
        return False

    def create(self, kind, system, messages, tools=None, **kwargs):
        self.calls.append({"system": system, "messages": messages, **kwargs})
        if self.error:
            raise LLMError("boom")
        return mock_text(self.draft)


def test_mock_message_uses_db_values(offers, ctx):
    offer = offers["OF0001"]  # CART10, 10% off, min cart 499
    msg = write_offer_message(offer, ctx)
    assert "CART10" in msg and "10% off" in msg and "₹499" in msg
    assert "{" not in msg and len(msg) <= MAX_MESSAGE_CHARS


@pytest.mark.parametrize("offer_id", [f"OF{i:04d}" for i in range(1, 21)])
def test_every_sample_offer_gives_a_valid_message(offers, ctx, offer_id):
    offer = offers[offer_id]
    msg = write_offer_message(offer, ctx)
    assert offer.code in msg
    assert "{" not in msg and len(msg) <= MAX_MESSAGE_CHARS


def test_llm_numbers_are_rejected_and_template_used(offers, ctx):
    fake = FakeClient(draft="Hurry! Get 90% off with code FREE90 — {CODE} {DISCOUNT} {EXPIRY}")
    set_client(fake)
    msg = write_offer_message(offers["OF0001"], ctx)
    assert msg == templates.fallback_offer_message(offers["OF0001"], "cart_abandon")
    assert "FREE90" not in msg and "90%" not in msg


def test_llm_error_falls_back(offers, ctx):
    set_client(FakeClient(error=True))
    assert "CART10" in write_offer_message(offers["OF0001"], ctx)


def test_valid_llm_draft_is_filled(offers, ctx):
    set_client(FakeClient(draft="Your cart misses you! Enjoy {DISCOUNT} with {CODE} until {EXPIRY}."))
    msg = write_offer_message(offers["OF0001"], ctx)
    assert msg.startswith("Your cart misses you! Enjoy 10% off with CART10 until 31 Mar.")


def test_no_personal_data_sent_to_claude(offers, ctx):
    fake = FakeClient(draft="Enjoy {DISCOUNT} with {CODE} until {EXPIRY}.")
    set_client(fake)
    write_offer_message(offers["OF0005"], ctx)
    sent = str(fake.calls)
    assert "U00001" not in sent
    assert "PHONECOVER" not in sent          # Claude never even sees the real code
    assert "Samsung Galaxy M35" in sent      # ...but it does get product names for wording


@pytest.mark.parametrize("draft, why", [
    ("Enjoy {DISCOUNT} till {EXPIRY}.", "missing placeholder {CODE}"),
    ("Get 50% off: {CODE} {DISCOUNT} {EXPIRY}", "numbers"),
    ("Only ₹ savings {CODE} {DISCOUNT} {EXPIRY}", "numbers"),
    ("Last chance! {CODE} {DISCOUNT} {EXPIRY}", "false urgency"),
    ("Visit https://evil.example {CODE} {DISCOUNT} {EXPIRY}", "link"),
    ("Hi {NAME}! {CODE} {DISCOUNT} {EXPIRY}", "unknown placeholder"),
    ("x" * 401 + "{CODE} {DISCOUNT} {EXPIRY}", "too long"),
])
def test_validator_rejects_bad_drafts(offers, draft, why):
    result = validate_offer_text(draft, offers["OF0001"])
    assert not result.allowed
    assert re.search(why.split()[0], result.reason, re.I)


def test_product_title_digits_are_allowed(offers):
    # OF0005 is about "Samsung Galaxy M35 5G (6GB/128GB)" — its digits are a name, not a price.
    draft = "Love your Samsung Galaxy M35 5G (6GB/128GB)? Get {DISCOUNT} on a cover with {CODE}, till {EXPIRY}."
    assert not validate_offer_text(draft, offers["OF0005"]).allowed
    assert validate_offer_text(draft, offers["OF0005"], ["Samsung Galaxy M35 5G (6GB/128GB)"]).allowed


def test_price_placeholder_only_for_product_offers(offers):
    draft = "Now {PRICE}! {CODE} {DISCOUNT} {EXPIRY}"
    assert validate_offer_text(draft, offers["OF0005"]).allowed      # product offer -> {PRICE} ok
    assert not validate_offer_text(draft, offers["OF0001"]).allowed  # store-wide -> no price


def test_offer_wording_uses_a_tight_time_budget(offers, ctx):
    """One slow Claude request must not hold the pipeline for a minute: short timeout, 1 retry."""
    from llm import offer_writer

    fake = FakeClient(draft="Your cart misses you! Enjoy {DISCOUNT} with {CODE} until {EXPIRY}.")
    set_client(fake)
    write_offer_message(offers["OF0001"], ctx)
    (call,) = fake.calls
    assert call["timeout"] == offer_writer.TIMEOUT_S <= 10
    assert call["max_retries"] == offer_writer.MAX_RETRIES <= 1
    assert call["max_tokens"] == offer_writer.MAX_TOKENS
