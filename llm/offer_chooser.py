"""Offer chooser: Claude PICKS which rule-approved offer a shopper gets ("LLM proposes, rules decide").

    from llm.offer_chooser import choose_offer
    choice = choose_offer(shopper, candidates)   # -> OfferChoice(offer_id, reason, source, rejected)

How it stays safe:
1. Claude only sees offers that ALREADY passed every business rule (the shortlist), plus one
   extra option, "no_discount": a friendly reminder with no coupon (always within the rules,
   because it gives nothing away).
2. Claude answers with JSON {"offer_id": ..., "reason": ...}. Our code checks the answer:
   the id must be one of the options we gave it. Anything else (an invented id, broken JSON,
   an API error, a timeout) is REJECTED, written to audit_log, and the rec engine's top pick
   is used instead. So the AI can make a choice better, never make it unsafe.
3. No personal data goes to Claude: budget band, favourite category names, order count,
   and the offers' non-personal facts only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from common.logging_utils import audit, get_logger
from llm.client import LLMError, get_client, mock_text, text_of

log = get_logger("llm.offer_chooser")

NO_DISCOUNT = "no_discount"
MAX_TOKENS = 200
TIMEOUT_S = 10.0
MAX_RETRIES = 1
MAX_REASON_CHARS = 160

CHOOSER_SYSTEM_PROMPT = """You are the offer-selection agent of an Indian online marketplace.
You get a shopper summary and a shortlist of offers. Every offer on the list has already \
passed the store's business rules (discount caps, margin floor, frequency limits), so any \
of them is allowed. Your job: pick the ONE option that is most likely to change this \
shopper's behaviour at the lowest cost to the store.

How to decide:
- Prefer offers on the exact product or category the shopper showed interest in.
- A price-sensitive (low budget band) shopper responds to bigger discounts; a premium \
(high budget band) shopper who orders often would probably buy anyway, so a smaller offer \
or "no_discount" (a friendly reminder without a coupon) saves money.
- When two offers are similar, choose the cheaper one for the store (lower est_cost_inr).

Reply with ONLY a JSON object, no other text:
{"offer_id": "<one id from the list, or no_discount>", "reason": "<max 20 words, plain English, for the marketing team>"}"""


@dataclass
class OfferChoice:
    """What was chosen and by whom. `rejected` explains why an AI answer was not used."""

    offer_id: str
    reason: str
    source: str  # "ai" | "ranker" (rec engine top pick) | "rule"
    rejected: str = ""


def _prompt(shopper: dict, candidates: list[dict]) -> str:
    return ("Shopper:\n" + json.dumps(shopper, ensure_ascii=False) +
            "\n\nOptions (best rec-engine score first):\n" +
            "\n".join(json.dumps(c, ensure_ascii=False) for c in candidates) +
            f'\n{{"offer_id": "{NO_DISCOUNT}", "what": "friendly reminder, no coupon", "est_cost_inr": 0}}')


def _mock_reply(shopper: dict, candidates: list[dict]):
    """Mock mode (no API key): a simple, explainable version of the same policy."""
    def fake(messages, tools):
        band = shopper.get("budget_band", "mid")
        if band == "high" and len(candidates) > 1:
            pick = min(candidates, key=lambda c: c.get("est_cost_inr", 0))
            reason = "Premium shopper likely to buy anyway, so the cheapest rule-approved offer is enough."
        else:
            pick = candidates[0]
            reason = "Best match for what this shopper looked at, within the discount rules."
        return mock_text(json.dumps({"offer_id": pick["offer_id"], "reason": reason}))
    return fake


def parse_choice(text: str) -> tuple[str, str]:
    """('offer_id', 'reason') from Claude's reply. Raises ValueError when it is not valid JSON."""
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise ValueError("no JSON object in the reply")
    data = json.loads(match.group(0))
    offer_id = str(data.get("offer_id") or "").strip()
    reason = re.sub(r"https?://\S+|\s+", " ", str(data.get("reason") or "")).strip()
    return offer_id, reason[:MAX_REASON_CHARS] or "chosen by the AI"


def choose_offer(shopper: dict, candidates: list[dict], user_id: str | None = None,
                 allow_no_discount: bool = True) -> OfferChoice:
    """Let Claude pick among `candidates` (rule-approved, best first). Never raises.

    shopper:    non-personal facts, e.g. {"budget_band": "low", "likes": "Fashion", "orders_last_120_days": 2}
    candidates: [{"offer_id": "OF0042", "what": "10% off", "product": "...", "est_cost_inr": 120, ...}, ...]
    """
    top = OfferChoice(candidates[0]["offer_id"], "rec engine's top pick", "ranker") if candidates else None
    if not candidates:
        return OfferChoice(NO_DISCOUNT, "no rule-approved offer", "rule")
    allowed = {c["offer_id"] for c in candidates} | ({NO_DISCOUNT} if allow_no_discount else set())
    client = get_client()
    try:
        response = client.create(
            "fast", system=CHOOSER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _prompt(shopper, candidates)}],
            max_tokens=MAX_TOKENS, effort="low", mock=_mock_reply(shopper, candidates),
            timeout=TIMEOUT_S, max_retries=MAX_RETRIES,
        )
        offer_id, reason = parse_choice(text_of(response))
    except (LLMError, ValueError) as exc:
        return _reject(top, user_id, f"AI unavailable or unreadable answer ({exc})", None)
    if offer_id not in allowed:
        # The AI proposed something the rules never approved (made-up id, or no_discount where
        # it isn't allowed): the rules win, the rec engine's pick is used.
        return _reject(top, user_id, f"AI proposed '{offer_id}', which is not on the rule-approved list", offer_id)
    audit("llm", user_id, "choose_offer", "allowed", reason,
          {"offer_id": offer_id, "options": sorted(allowed), "mock": client.is_mock})
    return OfferChoice(offer_id, reason, "ai")


def _reject(fallback: OfferChoice, user_id: str | None, why: str, proposed: str | None) -> OfferChoice:
    log.warning("AI offer choice rejected for %s: %s", user_id, why)
    audit("llm", user_id, "choose_offer", "rejected", why,
          {"proposed": proposed, "used_instead": fallback.offer_id})
    fallback.rejected = why
    return fallback
