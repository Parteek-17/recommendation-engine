"""Offer message writer: Claude writes the WORDING, our code inserts every value.

    from llm import write_offer_message
    text = write_offer_message(offer, user_context)

How it stays safe:
1. Claude only gets non-personal facts (offer type, product/category names, trigger, the
   user's favourite category names). No name, phone, chat_id or user_id.
2. Claude must write placeholders {CODE} {DISCOUNT} {EXPIRY} (and maybe {PRICE}) instead of
   real values — it never types a code, price, percentage or date.
3. We validate the draft (length, placeholders, no digits/₹/%, no fake urgency, no links).
4. We fill the placeholders from the DB and add the T&C (min. cart) ourselves.
5. Anything wrong (no key, API error, invalid draft) -> plain template from templates.py.
"""

from __future__ import annotations

import re
import zlib

from common.logging_utils import audit, get_logger
from common.schemas import GuardrailResult, Offer, UserContext
from llm import catalog, templates
from llm.client import LLMError, get_client, mock_text, text_of
from llm.prompts import OFFER_SYSTEM_PROMPT, offer_user_prompt

log = get_logger("llm.offer_writer")

MAX_MESSAGE_CHARS = 400
# Claude's draft is ~60 tokens. A tight time budget keeps one slow/stuck request from holding
# the pipeline for a minute (default client: 20 s x 3 attempts); on timeout we use the template.
MAX_TOKENS = 300
TIMEOUT_S = 10.0
MAX_RETRIES = 1
BANNED_PHRASES = (
    "hurry", "last chance", "only today", "today only", "selling fast", "selling out", "few left", "limited stock",
    "lowest price", "best price", "cheapest", "guaranteed", "guarantee", "free gift", "act now", "don't miss out",
    "ends soon", "expires soon", "once in a lifetime", "never before", "100%", "risk-free", "jaldi karo", "aakhri mauka",
)

_OFFER_KIND = {
    "pct_off": "percentage discount", "flat_off": "flat rupee discount", "free_delivery": "free delivery",
    "bundle": "discount when buying two products together", "buy_again": "discount on buying a product again",
    "points": "bonus reward points",
}
_TRIGGER_WHY = {
    "cart_abandon": "the user left items in their cart", "price_drop": "a product they viewed got cheaper",
    "repurchase_due": "they usually reorder this item around now", "post_purchase": "they just bought something",
    "win_back": "they haven't shopped for a while", "festival": "festive season sale",
}


def _value(enum_or_str) -> str:
    return getattr(enum_or_str, "value", enum_or_str) or ""


def offer_facts(offer: Offer, user_context: UserContext) -> tuple[dict, list[str]]:
    """Non-personal facts for Claude + the names (titles/categories) Claude may copy verbatim."""
    names: list[str] = []
    product = catalog.get_product(offer.product_id) if offer.product_id else None
    bundle = catalog.get_product(offer.bundle_product_id) if offer.bundle_product_id else None
    category = catalog.category_name(offer.category_id) if offer.category_id else ""
    for n in (product and product["title"], bundle and bundle["title"], category):
        if n:
            names.append(str(n))
    liked = [catalog.category_name(c) for c in user_context.top_categories[:3]]
    names += liked
    trigger = _value(user_context.trigger_type) or _value(offer.trigger_type)
    facts = {
        "offer_kind": _OFFER_KIND.get(_value(offer.offer_type), "special offer"),
        "product": product["title"] if product else "",
        "bundled_with": bundle["title"] if bundle else "",
        "category": category or ("" if product else "whole store"),
        "why_now": _TRIGGER_WHY.get(trigger, ""),
        "shopper_likes": ", ".join(liked),
        "gift_shopping": "yes" if user_context.is_gift_mode else "",
        "price_placeholder_available": "yes, {PRICE} is the product price" if product else "no, do not use {PRICE}",
    }
    return facts, names


def validate_offer_text(text: str, offer: Offer, allowed_names: list[str] | None = None) -> GuardrailResult:
    """Check Claude's draft BEFORE placeholders are filled."""
    if not text:
        return GuardrailResult(allowed=False, reason="empty draft")
    if len(text) > MAX_MESSAGE_CHARS:
        return GuardrailResult(allowed=False, reason=f"too long ({len(text)} chars)")
    for ph in templates.required_placeholders(offer):
        if text.count(ph) < 1:
            return GuardrailResult(allowed=False, reason=f"missing placeholder {ph}")
        if text.count(ph) > 2:
            return GuardrailResult(allowed=False, reason=f"placeholder {ph} repeated")
    allowed = set(templates.placeholder_values(offer))
    unknown = [p for p in re.findall(r"\{[^{}]*\}", text) if p not in allowed]
    if unknown:
        return GuardrailResult(allowed=False, reason=f"unknown placeholder(s) {unknown}")

    # Remove placeholders and product/category names (titles like 'Galaxy M35 5G' contain digits),
    # then nothing number-like may remain — those would be values Claude made up.
    rest = text
    for ph in allowed:
        rest = rest.replace(ph, " ")
    for name in sorted(allowed_names or [], key=len, reverse=True):
        rest = re.sub(re.escape(name), " ", rest, flags=re.I)
    if re.search(r"\d|₹|%|\brs\.?\b|\binr\b|\brupees?\b|\bpercent\b", rest, re.I):
        return GuardrailResult(allowed=False, reason="draft contains its own numbers/prices")
    if re.search(r"https?://|www\.|t\.me/|@\w", rest, re.I):
        return GuardrailResult(allowed=False, reason="draft contains a link or handle")
    low = rest.lower()
    banned = [p for p in BANNED_PHRASES if p in low]
    if banned:
        return GuardrailResult(allowed=False, reason=f"false urgency/claim: {banned[0]!r}")
    return GuardrailResult(allowed=True, reason="ok")


# Canned drafts for mock mode (same placeholder rules as Claude's output).
_MOCK_DRAFTS = {
    "cart_abandon": ["Still thinking it over? Your cart is waiting — enjoy {DISCOUNT} with code {CODE} till {EXPIRY}. 🛒",
                     "Your picks are still in the cart. Use {CODE} for {DISCOUNT}, valid till {EXPIRY}."],
    "price_drop": ["Good news: something you liked just got cheaper. Grab {DISCOUNT} with {CODE} before {EXPIRY}."],
    "repurchase_due": ["Running low? Restock now and get {DISCOUNT} with code {CODE} (valid till {EXPIRY})."],
    "post_purchase": ["Thanks for shopping with us! Here's {DISCOUNT} on something that goes well with it — code {CODE}, till {EXPIRY}."],
    "win_back": ["We've missed you! Come back for {DISCOUNT} with code {CODE}, valid till {EXPIRY}. 💛"],
    "festival": ["Celebrate the season with {DISCOUNT}! Use code {CODE} till {EXPIRY}. 🪔"],
}


def _mock_offer_reply(offer: Offer, trigger: str):
    def fake(messages, tools):
        drafts = _MOCK_DRAFTS.get(trigger, ["A little treat for you: {DISCOUNT} with code {CODE}, till {EXPIRY}."])
        return mock_text(drafts[zlib.crc32(offer.offer_id.encode()) % len(drafts)])
    return fake


def write_offer_message(offer: Offer, user_context: UserContext) -> str:
    """Final, ready-to-send offer text (always valid: falls back to a template if needed)."""
    trigger = _value(user_context.trigger_type) or _value(offer.trigger_type)
    facts, names = offer_facts(offer, user_context)
    client = get_client()
    try:
        response = client.create(
            "fast", system=OFFER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": offer_user_prompt(facts)}],
            max_tokens=MAX_TOKENS, effort="low", mock=_mock_offer_reply(offer, trigger),
            timeout=TIMEOUT_S, max_retries=MAX_RETRIES,
        )
        draft = text_of(response)
    except LLMError as exc:
        return _fallback(offer, trigger, user_context, f"llm error: {exc}")

    check = validate_offer_text(draft, offer, names)
    if not check.allowed:
        return _fallback(offer, trigger, user_context, f"invalid draft: {check.reason}")
    message = templates.fill_placeholders(draft, offer) + templates.terms_suffix(offer)
    if len(message) > MAX_MESSAGE_CHARS:
        return _fallback(offer, trigger, user_context, "too long after filling values")
    audit("llm", user_context.user_id, "write_offer_message", "allowed",
          "mock draft" if client.is_mock else "claude draft", {"offer_id": offer.offer_id})
    return message


def _fallback(offer: Offer, trigger: str, user_context: UserContext, reason: str) -> str:
    log.warning("offer %s: using template (%s)", offer.offer_id, reason)
    audit("llm", user_context.user_id, "write_offer_message", "fallback", reason, {"offer_id": offer.offer_id})
    return templates.fallback_offer_message(offer, trigger)
