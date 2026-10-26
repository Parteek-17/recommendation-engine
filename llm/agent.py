"""Chat shopping agent: chat(user_id, message) -> ChatReply.

One chat turn, step by step:

    user text
      -> check_input        (block injection, fake discounts, off-topic, toxicity... -> polite refusal)
      -> mask_pii           (phone / email / card / Aadhaar never reach Claude)
      -> Claude + tools     (up to MAX_TOOL_CALLS; tools are bound to THIS user only)
      -> render products    ([P00007] -> real title, price, rating from the DB)
      -> validate_output    (IDs exist, prices/ratings/codes match DB, nothing for minors, no leaks)
           fail -> drop the bad sentences and re-check -> still bad -> plain recommendations
      -> ChatReply(text, product_ids, buttons)

"LLM proposes, rules decide": Claude chooses which tools to call and writes the words; our
code decides what facts exist and whether the reply may be sent.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict

from common.logging_utils import audit, get_logger
from common.schemas import ChatReply
from llm import catalog, templates
from llm.client import LLMError, get_client, text_of
from llm.guardrails import check_input, mask_pii, reason_code, validate_output
from llm.mock_brain import mock_reply
from llm.prompts import CHAT_SYSTEM_PROMPT
from llm.tools import TOOL_SCHEMAS, ToolContext, get_recommendations, run_tool

log = get_logger("llm.agent")

MAX_TOOL_CALLS = 5         # per user turn
TURN_TIMEOUT_S = 60        # wall-clock budget for one user turn
HISTORY_MESSAGES = 6       # short memory (text only) per user, kept in this process
MAX_PRODUCTS_SHOWN = 5
PRODUCT_BUTTONS = ["Show more", "Not interested"]

_history: dict[str, list[dict]] = defaultdict(list)


def reset_history(user_id: str | None = None) -> None:
    if user_id is None:
        _history.clear()
    else:
        _history.pop(user_id, None)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def chat(user_id: str, message: str) -> ChatReply:
    """Answer one user message. Always returns a safe ChatReply (never raises for user input)."""
    check = check_input(message, user_id=user_id)
    if not check.allowed:
        audit("llm", user_id, "chat_input", "blocked", check.reason)
        return ChatReply(text=templates.refusal_text(reason_code(check), message))

    masked, pii = mask_pii(message)
    if pii:
        audit("llm", user_id, "chat_input", "masked", f"pii masked: {', '.join(pii)}")

    ctx = ToolContext(user_id=user_id)
    try:
        draft = _run_agent(ctx, masked)
    except LLMError as exc:
        return _safe_fallback(user_id, message, f"llm error: {exc}")

    # Strict check (no "numbers the user typed" allowance): the Main API re-runs
    # validate_output(reply) on its own, so the reply must pass with no extra context.
    reply = _render(ctx, draft)
    result = validate_output(reply, user_id=user_id, allowed_codes=ctx.offer_codes)
    if not result.allowed:
        log.warning("reply for %s failed grounding (%s); stripping", user_id, result.reason)
        reply = _strip_ungrounded(ctx, draft, masked)
        retry = validate_output(reply, user_id=user_id, allowed_codes=ctx.offer_codes)
        if not retry.allowed or not reply.text.strip():
            return _safe_fallback(user_id, message, f"output blocked: {result.reason}")
        audit("llm", user_id, "chat_reply", "stripped", result.reason)

    _remember(user_id, masked, reply.text)
    audit("llm", user_id, "chat_reply", "allowed", "ok",
          {"tools": ctx.tool_calls, "products": reply.product_ids, "mock": get_client().is_mock})
    return reply


# ---------------------------------------------------------------------------
# The tool-use loop
# ---------------------------------------------------------------------------


def _run_agent(ctx: ToolContext, user_text: str) -> str:
    """Let Claude call tools until it writes a final answer. Returns Claude's final text."""
    client = get_client()
    messages: list = list(_history[ctx.user_id]) + [{"role": "user", "content": user_text}]
    started = time.monotonic()
    tool_calls = 0
    while True:
        if time.monotonic() - started > TURN_TIMEOUT_S:
            raise LLMError("turn timed out")
        response = client.create(
            "smart", system=CHAT_SYSTEM_PROMPT, messages=messages, tools=TOOL_SCHEMAS,
            max_tokens=2048, effort="low", allow_tools=tool_calls < MAX_TOOL_CALLS, mock=mock_reply,
        )
        tool_uses = [b for b in response.content if getattr(b, "type", "") == "tool_use"]
        if response.stop_reason != "tool_use" or not tool_uses:
            if response.stop_reason == "max_tokens":
                raise LLMError("reply was cut off (max_tokens)")
            return text_of(response)

        # Keep Claude's whole reply (incl. thinking blocks) unchanged, then answer EVERY tool call
        # in ONE user message — the API expects exactly that.
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in tool_uses:
            tool_calls += 1
            if tool_calls > MAX_TOOL_CALLS:
                output, is_error = {"error": "tool limit reached for this turn; answer with what you have"}, True
            else:
                output, is_error = run_tool(ctx, block.name, block.input)
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": json.dumps(output, ensure_ascii=False, default=str), "is_error": is_error})
        messages.append({"role": "user", "content": results})


# ---------------------------------------------------------------------------
# Turning Claude's draft into a reply (facts from the DB only)
# ---------------------------------------------------------------------------

_REF = re.compile(r"\[?\b(P\d{5})\b\]?")


def _pick_products(ctx: ToolContext, draft: str) -> list[str]:
    """Products to show: the ones Claude referenced (if real), else the latest tool results."""
    mentioned = [p for p in dict.fromkeys(m.group(1) for m in _REF.finditer(draft)) if catalog.product_exists(p)]
    chosen = mentioned or ctx.last_results[:3]
    if catalog.is_minor(ctx.user_id):
        chosen = [p for p in chosen if not catalog.is_age_restricted(p)]
    return chosen[:MAX_PRODUCTS_SHOWN]


def _prose(draft: str) -> str:
    """Claude's text with [P00007] replaced by the real product title."""
    def title(m: re.Match) -> str:
        p = catalog.get_product(m.group(1))
        return str(p["title"]) if p else m.group(0)
    return _REF.sub(title, draft).strip()


def _render(ctx: ToolContext, draft: str, prose: str | None = None) -> ChatReply:
    product_ids = _pick_products(ctx, draft)
    text = _prose(draft) if prose is None else prose
    cards = "\n".join(f"• {catalog.product_card(p)}" for p in product_ids)
    full = f"{text}\n{cards}".strip() if cards else text
    buttons = list(ctx.buttons) or (PRODUCT_BUTTONS if product_ids else [])
    return ChatReply(text=full, product_ids=product_ids, buttons=buttons)


def _strip_ungrounded(ctx: ToolContext, draft: str, user_text: str) -> ChatReply:
    """Drop every sentence of Claude's prose that fails the grounding check on its own."""
    product_ids = _pick_products(ctx, draft)
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", _prose(draft)):
        probe = ChatReply(text=sentence, product_ids=product_ids)
        if sentence.strip() and validate_output(probe, ctx.user_id, allowed_codes=ctx.offer_codes).allowed:
            kept.append(sentence.strip())
    prose = " ".join(kept) or (templates.refusal_text("fallback", user_text) if product_ids else "")
    return _render(ctx, draft, prose=prose)


def _safe_fallback(user_id: str, user_text: str, reason: str) -> ChatReply:
    """No LLM: plain recommendations from the rec engine, rendered from the DB."""
    audit("llm", user_id, "chat_reply", "fallback", reason)
    try:
        recs = get_recommendations(user_id, k=6)
    except Exception:
        recs = []
    ids = [r.product_id for r in recs
           if catalog.product_exists(r.product_id)
           and not (catalog.is_minor(user_id) and catalog.is_age_restricted(r.product_id))][:3]
    if not ids:
        return ChatReply(text=templates.refusal_text("default", user_text))
    cards = "\n".join(f"• {catalog.product_card(p)}" for p in ids)
    return ChatReply(text=f"{templates.refusal_text('fallback', user_text)}\n{cards}", product_ids=ids,
                     buttons=PRODUCT_BUTTONS)


def _remember(user_id: str, user_text: str, reply_text: str) -> None:
    """Keep a short text-only history so follow-ups like 'show similar to the second one' work."""
    history = _history[user_id]
    history += [{"role": "user", "content": user_text}, {"role": "assistant", "content": reply_text}]
    del history[:-HISTORY_MESSAGES]
