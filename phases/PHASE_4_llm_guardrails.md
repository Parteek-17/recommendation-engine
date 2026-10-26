# PHASE 4 — LLM Layer + Guardrails (parallel)

> Paste everything below into a new chat with the project folder selected. Phase 0 must be done.

---

You are building **Phase 4 (LLM layer + guardrails)** of the E-commerce Recommendation Engine.

**Before anything:** read `CLAUDE.md` (Agentic AI + guardrails decision, Module Contracts → `llm`, rules), `phases/NOTES.md`. Use `common/stubs.py` for `reco`/`offers` until they exist. I am a beginner — explain what tool use, system prompts and guardrails are as you go.

## Goal
All Claude API usage lives here: (a) offer message writer (wording only), (b) chat shopping agent with tools, (c) input/output guardrails, (d) fallbacks.

## You own
`llm/`, `tests/llm/`. Do not edit `common/`.

## Principle
**"LLM proposes, rules decide."** Claude never produces prices, stock, product IDs, offer codes, discount values or expiry dates on its own — those are inserted from the DB or validated against it.

## Build
1. **Client** (`llm/client.py`): wrapper around the `anthropic` SDK; models from `CLAUDE_MODEL_FAST` / `CLAUDE_MODEL_SMART`; timeout, 2 retries, token limits; **mock mode** automatically when `ANTHROPIC_API_KEY` is empty (returns canned responses) so others can develop without a key. Never logs the key or full prompts with personal data.
2. **Offer message writer** (`llm/offer_writer.py`): `write_offer_message(offer, user_context) -> str`.
   - Claude gets: offer type, product/category *names*, trigger, user's top categories, tone rules. **No personal data** (no name, phone, chat_id).
   - Claude writes text containing placeholders `{CODE}`, `{DISCOUNT}`, `{EXPIRY}`, `{PRICE}`; our code fills them from the DB.
   - Validate: length ≤ 400 chars, all required placeholders present, no other numbers/₹/% typed by Claude, no false urgency/claims. Invalid or error → **plain template fallback** (`llm/templates.py`).
3. **Input guardrails** (`llm/guardrails.py` → `check_input`): length/rate limit, prompt-injection patterns ("ignore previous instructions", "you are now…", "system prompt"), off-topic (non-shopping) detection, PII masking (phone, email, card numbers, Aadhaar-like numbers), toxicity keywords. Hindi/Hinglish friendly.
4. **Chat agent** (`llm/agent.py` → `chat(user_id, message) -> ChatReply`): Claude tool use with ONLY these tools, mapped to `reco`/`offers` functions: `search_products`, `get_recommendations`, `get_similar`, `get_complements`, `compare_products`, `explain_recommendation`, `get_my_offers`, `add_to_cart` (returns a "please confirm" button — never executes directly). No payment/order tool exists. Max 5 tool calls per turn, timeout per turn. Tool inputs validated with pydantic; agent can only access the current `user_id`.
5. **System prompt** (`llm/prompts.py`): shopping-only scope, never invent products/prices, always use tools for facts, short replies, reply in user's language, refuse sensitive inferences (health, religion, pregnancy etc.).
6. **Output guardrails** (`validate_output`): every product_id exists in catalog; prices/ratings in text match DB (or are stripped and re-rendered from DB); no age-restricted items for minors; no leaked system prompt; on failure → safe fallback reply with plain recommendations from `reco`.
7. **Red-team suite** (`tests/llm/redteam_prompts.yaml` + `python -m llm.redteam`): ≥ 50 adversarial prompts (injection, fake discount requests, "give me 90% off", off-topic, PII, other user's data, jailbreaks). Report block rate to `llm/redteam_results.md`.

## Done when
- [ ] Works in mock mode with no key, and in real mode with a key
- [ ] Offer messages never contain LLM-typed codes/prices; placeholders filled from DB
- [ ] Agent answers "earphones under ₹2000" with real catalog products only
- [ ] Red-team block rate reported (target ≥ 95%); all guardrails unit-tested
- [ ] `llm/README.md` written; Phase 4 ticked in `phases/PROGRESS.md`
