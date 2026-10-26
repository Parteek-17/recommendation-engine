"""Plain, LLM-free text: offer fallback messages, placeholder values, and refusal replies.

These are used whenever Claude is unavailable, fails validation, or a guardrail blocks a
message. Every number here comes from the DB (via the Offer object / catalog), so a
template message is always correct — just less personal than Claude's wording.
"""

from __future__ import annotations

import re

from common.schemas import Offer
from llm import catalog

# ---------------------------------------------------------------------------
# Offer placeholders: Claude writes {CODE}, {DISCOUNT}, {EXPIRY}, {PRICE}; we fill them.
# ---------------------------------------------------------------------------

ALL_PLACEHOLDERS = ("{CODE}", "{DISCOUNT}", "{EXPIRY}", "{PRICE}")


def discount_text(offer: Offer) -> str:
    """Human wording of the offer value, e.g. '10% off', '₹100 off', '200 bonus points'."""
    v = offer.value
    return {
        "pct_off": f"{v:g}% off",
        "flat_off": f"{catalog.format_inr(v)} off",
        "free_delivery": "free delivery",
        "bundle": f"{v:g}% off the combo",
        "buy_again": f"{v:g}% off",
        "points": f"{v:g} bonus points",
    }.get(getattr(offer.offer_type, "value", offer.offer_type), "a special offer")


def offer_product_id(offer: Offer) -> str | None:
    """The product an offer is about (if any) — used for the {PRICE} placeholder."""
    return offer.product_id or None


def placeholder_values(offer: Offer) -> dict[str, str]:
    """DB values for each placeholder. {PRICE} only exists when the offer is about one product."""
    values = {
        "{CODE}": offer.code,
        "{DISCOUNT}": discount_text(offer),
        "{EXPIRY}": offer.expires_at.strftime("%d %b"),
    }
    pid = offer_product_id(offer)
    product = catalog.get_product(pid) if pid else None
    if product is not None:
        values["{PRICE}"] = catalog.format_inr(product["price"])
    return values


def required_placeholders(offer: Offer) -> list[str]:
    """Placeholders a message for this offer MUST contain."""
    return ["{CODE}", "{DISCOUNT}", "{EXPIRY}"]


def fill_placeholders(text: str, offer: Offer) -> str:
    for ph, value in placeholder_values(offer).items():
        text = text.replace(ph, value)
    return text


def terms_suffix(offer: Offer) -> str:
    """Conditions the user must know, straight from the DB (added after Claude's text)."""
    return f" Min. cart {catalog.format_inr(offer.min_cart_value)}." if offer.min_cart_value else ""


# ---------------------------------------------------------------------------
# Offer fallback (used when Claude is off, fails, or writes something invalid)
# ---------------------------------------------------------------------------

_TRIGGER_OPENERS = {
    "cart_abandon": "You left something in your cart.",
    "price_drop": "Good news — a price just dropped.",
    "repurchase_due": "Running low? Time to restock.",
    "post_purchase": "Thanks for your order! Here's something that goes with it.",
    "win_back": "We've missed you!",
    "festival": "Festive offer for you.",
}


def fallback_offer_message(offer: Offer, trigger_type: str | None = None) -> str:
    """A plain message with every value filled from the DB."""
    trigger = trigger_type or (offer.trigger_type.value if offer.trigger_type else "")
    opener = _TRIGGER_OPENERS.get(trigger, "A special offer for you.")
    about = ""
    pid = offer_product_id(offer)
    if pid and catalog.get_product(pid):
        about = f" on {catalog.get_product(pid)['title']}"
    elif offer.category_id:
        about = f" on {catalog.category_name(offer.category_id)}"
    template = f"{opener} Get {{DISCOUNT}}{about} with code {{CODE}}, valid till {{EXPIRY}}."
    return fill_placeholders(template, offer) + terms_suffix(offer)


# ---------------------------------------------------------------------------
# Chat refusals (one per guardrail reason, English + Hinglish)
# ---------------------------------------------------------------------------

_HINGLISH_WORDS = {
    "kya", "hai", "hain", "mujhe", "chahiye", "dikhao", "batao", "kaunsa", "kaun", "sasta", "accha",
    "acha", "ke", "ka", "ki", "liye", "nahi", "haan", "karo", "kar", "bhai", "yaar", "mera", "meri",
    "tum", "aap", "kuch", "wala", "wali", "andar", "neeche", "tak", "se", "mein", "main", "hoon", "ho", "jao",
    "bhool", "bhul", "pichle", "saare", "sab", "bata", "chalo", "kyun", "kaise", "kitna", "paas", "mere", "tera",
}


def detect_language(text: str) -> str:
    """'hi' (Devanagari), 'hinglish' (Hindi in Latin letters) or 'en'."""
    if re.search(r"[\u0900-\u097f]", text or ""):
        return "hi"
    words = set(re.findall(r"[a-z]+", (text or "").lower()))
    return "hinglish" if len(words & _HINGLISH_WORDS) >= 2 else "en"


_REFUSALS = {
    "en": {
        "default": "Sorry, I can't help with that. I can help you find products, compare them, or show your offers.",
        "off_topic": "I'm your shopping assistant, so I can only help with products, prices and offers. What are you shopping for?",
        "prompt_injection": "I can't change how I work. Happy to help you shop though — what are you looking for?",
        "discount_manipulation": "I can't create discounts or codes. I can show you the real offers available to you — just ask \"my offers\".",
        "other_user_data": "I can only help with your own account.",
        "sensitive_inference": "I don't make guesses about personal topics like health, religion or family plans. I can help you find products though!",
        "toxicity": "Let's keep it friendly. How can I help you shop today?",
        "self_harm": "I'm really sorry you're feeling this way. You don't have to face it alone — please call Tele-MANAS at 14416 (free, 24x7) or talk to someone you trust.",
        "harmful": "I can't help with that. I can help you find products in our store.",
        "too_long": "That message is too long. Please send a shorter question.",
        "empty": "Please type what you're looking for — e.g. \"earphones under ₹2000\".",
        "rate_limited": "You're sending messages very fast. Please wait a minute and try again.",
        "fallback": "Here are some picks for you:",
    },
    "hinglish": {
        "default": "Sorry, isme main help nahi kar sakta. Main products dhoondhne, compare karne aur offers dikhane mein help kar sakta hoon.",
        "off_topic": "Main aapka shopping assistant hoon — sirf products, prices aur offers mein help kar sakta hoon. Kya dhoondh rahe ho?",
        "prompt_injection": "Main apne rules nahi badal sakta. Shopping mein help kar deta hoon — kya chahiye?",
        "discount_manipulation": "Main discount ya code khud nahi bana sakta. Aapke liye jo real offers hain woh dikha sakta hoon — \"my offers\" likho.",
        "other_user_data": "Main sirf aapke apne account mein help kar sakta hoon.",
        "sensitive_inference": "Main health, religion jaise personal topics pe guess nahi karta. Products dhoondhne mein help kar deta hoon!",
        "toxicity": "Chalo friendly rehte hain. Aaj kya shopping karni hai?",
        "self_harm": "Mujhe sach mein afsos hai ki aap aisa mehsoos kar rahe ho. Aap akele nahi ho — Tele-MANAS ko 14416 pe call karo (free, 24x7) ya kisi apne se baat karo.",
        "harmful": "Isme main help nahi kar sakta. Store ke products dhoondhne mein help kar sakta hoon.",
        "too_long": "Message bahut lamba hai. Thoda chhota sawaal bhejo.",
        "empty": "Kya dhoondh rahe ho likho — jaise \"₹2000 ke andar earphones\".",
        "rate_limited": "Bahut jaldi messages aa rahe hain. Ek minute baad try karo.",
        "fallback": "Aapke liye kuch picks:",
    },
}
_REFUSALS["hi"] = _REFUSALS["hinglish"]  # Devanagari users get Hinglish (simple, readable)


def refusal_text(reason_code: str, user_text: str = "") -> str:
    """Friendly reply for a blocked message, in the user's language."""
    msgs = _REFUSALS[detect_language(user_text)]
    return msgs.get(reason_code, msgs["default"])
