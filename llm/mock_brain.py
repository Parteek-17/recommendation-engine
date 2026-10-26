"""A tiny rule-based stand-in for Claude, used by the chat agent in mock mode (no API key).

It speaks the same "protocol" as Claude: first it asks for ONE tool (a tool_use reply), then,
once it sees the tool result, it writes a short answer that refers to products as [P00007].
That way the real agent loop, tool validation and output guardrails all run in mock mode.
"""

from __future__ import annotations

import json
import re

from llm.client import MockMessage, mock_text, mock_tool_call
from llm.templates import detect_language

_PRICE_CAP = re.compile(
    r"(?:under|below|less than|within|upto|up to|max|budget)\s*(?:of\s*)?(?:₹|rs\.?|inr)?\s*(\d[\d,]*)"
    r"|(?:₹|rs\.?|inr)?\s*(\d[\d,]*)\s*(?:ke andar|ke neeche|se kam|tak|ke under)", re.I)


def parse_max_price(text: str) -> float | None:
    m = _PRICE_CAP.search(text)
    return float((m.group(1) or m.group(2)).replace(",", "")) if m else None


def decide_tool(text: str) -> tuple[str, dict]:
    """Pick one tool + input from the user's message (keyword rules)."""
    low = text.lower()
    ids = re.findall(r"\bP\d{5}\b", text.upper())
    has = lambda *words: any(w in low for w in words)  # noqa: E731
    if ids and len(ids) >= 2 and has("compare", "vs", "versus", "difference", "better"):
        return "compare_products", {"product_ids": ids[:4]}
    if ids and has("add", "cart", "daal"):
        return "add_to_cart", {"product_id": ids[0], "quantity": 1}
    if ids and has("why", "kyun", "kyon", "reason"):
        return "explain_recommendation", {"product_id": ids[0]}
    if ids and has("goes with", "go with", "complement", "accessor", "saath", "along with", "matching"):
        return "get_complements", {"product_id": ids[0]}
    if ids and has("similar", "like", "alternative", "jaisa", "jaise", "instead"):
        return "get_similar", {"product_id": ids[0]}
    if has("offer", "coupon", "discount", "code", "deal", "promo"):
        return "get_my_offers", {}
    if has("recommend", "suggest", "for me", "picks", "gift", "surprise", "what should i buy"):
        return "get_recommendations", {"k": 5, "gift_mode": has("gift", "tohfa")}
    tool_input: dict = {"query": text[:100], "k": 5}
    if (cap := parse_max_price(text)) is not None:
        tool_input["max_price"] = cap
    return "search_products", tool_input


def _last_tool_result(messages: list) -> tuple[str, dict] | None:
    """(tool name, parsed result) of the most recent tool call, if the last message holds results."""
    last = messages[-1]
    if last["role"] != "user" or not isinstance(last["content"], list):
        return None
    result = next((b for b in last["content"] if isinstance(b, dict) and b.get("type") == "tool_result"), None)
    if result is None:
        return None
    asked = messages[-2]["content"]
    name = next((b.name for b in asked if getattr(b, "type", "") == "tool_use"), "")
    try:
        return name, json.loads(result["content"])
    except (TypeError, ValueError):
        return name, {}


def _first_user_text(messages: list) -> str:
    for m in reversed(messages):
        if m["role"] == "user" and isinstance(m["content"], str):
            return m["content"]
    return ""


def mock_reply(messages: list, tools: list | None) -> MockMessage:
    """The fake Claude: tool call first, answer second."""
    user_text = _first_user_text(messages)
    hindi = detect_language(user_text) != "en"
    seen = _last_tool_result(messages)
    if seen is None:
        name, tool_input = decide_tool(user_text)
        return mock_tool_call(name, tool_input, preamble="Let me check the catalog.")

    name, result = seen
    if "error" in result:
        return mock_text("Maaf kijiye, woh nahi mil paya." if hindi else "Sorry, I couldn't find that product.")
    if name == "get_my_offers":
        codes = [o["code"] for o in result.get("offers", [])][:3]
        if not codes:
            return mock_text("You have no live offers right now.")
        return mock_text(("Aapke offers: " if hindi else "Your live offers: ") + ", ".join(codes) + ".")
    if name == "add_to_cart":
        pid = result["product"]["product_id"]
        return mock_text(f"Tap confirm to add [{pid}] to your cart." if not hindi else f"[{pid}] cart mein daalne ke liye confirm dabao.")
    items = result.get("products") or ([result["product"]] if "product" in result else [])
    if not items:
        return mock_text("Is budget mein kuch nahi mila." if hindi else "Sorry, I couldn't find matching products in our catalog.")
    refs = " ".join(f"[{i['product_id']}]" for i in items[:3])
    if name == "explain_recommendation":
        return mock_text(f"{result.get('reason', 'It matches your interests')}: [{items[0]['product_id']}].")
    return mock_text((f"Yeh options dekho: {refs}" if hindi else f"Here are some good options: {refs}"))
