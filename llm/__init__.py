"""LLM layer (Phase 4): all Claude API usage + guardrails.

Contract functions (CLAUDE.md -> Module Contracts -> llm):
    write_offer_message(offer, user_context) -> str
    chat(user_id, message) -> ChatReply
    check_input(text) -> GuardrailResult
    validate_output(reply) -> GuardrailResult
"""

from llm.agent import chat
from llm.guardrails import check_input, mask_pii, validate_output
from llm.offer_writer import write_offer_message

__all__ = ["chat", "check_input", "validate_output", "write_offer_message", "mask_pii"]
