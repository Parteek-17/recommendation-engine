# llm/ — LLM layer + guardrails (Phase 4)

All Claude API usage lives here: the **offer message writer**, the **chat shopping agent**, the
**input/output guardrails**, and the **fallbacks** used when Claude is off or misbehaves.

Principle: **"LLM proposes, rules decide."** Claude picks words and which tools to call. Every
price, rating, stock level, product ID, offer code, discount and expiry date comes from the DB.

## Contract functions

```python
from llm import write_offer_message, chat, check_input, validate_output

write_offer_message(offer, user_context) -> str    # ready-to-send text, always valid
chat(user_id, message) -> ChatReply                 # text + product_ids + buttons
check_input(text, user_id=None) -> GuardrailResult  # user_id enables the rate limit
validate_output(reply, user_id=None) -> GuardrailResult   # user_id enables the minor check
```

`GuardrailResult.reason` is always `"<code>: <detail>"` when blocked, e.g.
`"prompt_injection: ignore previous instructions"`. `llm.guardrails.reason_code(result)` returns the code.

## Mock mode vs real mode

| `.env` | What happens |
|---|---|
| `ANTHROPIC_API_KEY=` (empty) | **Mock mode.** No network. Offer writer uses canned drafts; the chat agent uses `mock_brain.py`, a rule-based stand-in that still goes through the *same* tool loop and guardrails. |
| `ANTHROPIC_API_KEY=sk-ant-...` | **Real mode.** Offer messages use `CLAUDE_MODEL_FAST` (default `claude-haiku-5-5`), chat uses `CLAUDE_MODEL_SMART` (default `claude-sonnet-5-5`). Both run at `effort: low` (short tasks); 20 s timeout and 2 retries per request. |

Any Claude error (network, bad key, refusal, cut-off) falls back to non-LLM output, so callers never need to handle it.

## Files

| File | What it does |
|---|---|
| `client.py` | The only place that calls the `anthropic` SDK. Mock mode, timeouts, retries, typed errors → `LLMError`. Never logs keys or prompts. |
| `prompts.py` | System prompts (chat agent "ShopSaathi" + offer copywriter) and `LEAK_MARKERS` used to detect prompt leaks. |
| `offer_writer.py` | `write_offer_message`: Claude writes `{CODE}` `{DISCOUNT}` `{EXPIRY}` (`{PRICE}`) placeholders, we validate and fill them from the DB. |
| `templates.py` | Plain fallback offer text, placeholder values, refusal replies (English + Hinglish), language detection. |
| `guardrails.py` | `check_input`, `mask_pii`, `validate_output`. |
| `tools.py` | The 8 allowlisted agent tools (pydantic-validated, bound to the current user). |
| `agent.py` | `chat()`: guardrails → tool loop → render products from DB → grounding check → fallback. |
| `mock_brain.py` | Fake Claude for mock mode. |
| `catalog.py` | Read-only DB lookups + `format_inr` (₹1,23,456 style) + product cards. |
| `redteam.py` | Red-team runner → `redteam_results.md`. |

## How the pieces work (beginner notes)

**System prompt:** the standing instructions Claude reads before every conversation (its job,
scope, rules). It's a strong nudge, not a wall, so everything important is also enforced in code.

**Tool use:** Claude can't look anything up by itself. We describe tools (name + JSON schema).
Claude answers "run `search_products` with `{query: 'earbuds', max_price: 2000}`", our code runs it,
sends back the result, and Claude writes the answer. The loop in `agent._run_agent`:

1. Send the conversation + tool list to Claude.
2. If Claude asks for tools, run each one (`tools.run_tool`), send all results back in one message.
3. Repeat until Claude answers in text. After 5 tool calls per turn, tools are switched off
   (`tool_choice: none`) and Claude must answer. The whole turn has a 60 s budget.

**Guardrails:**

| Layer | Where | What |
|---|---|---|
| Input | `check_input` | empty / >1000 chars / >12 msgs per minute; toxicity; self-harm (replies with Tele-MANAS 14416); harmful requests; prompt injection & jailbreaks (handles CAPS, l33t, spaced letters, zero-width chars, Hinglish, Devanagari); fake-discount demands; other users' data; sensitive inferences (health, religion, caste, pregnancy…); off-topic (only if no shopping words) |
| PII | `mask_pii` | phone, email, card, Aadhaar, PAN, UPI → `[PHONE]` etc. *before* Claude or logs see the text |
| Tools | `tools.py` | allowlist of 8 tools, no payment/order/refund tool, pydantic validation with `extra="forbid"`, no `user_id` parameter (bound to the logged-in user), `add_to_cart` only returns a confirm button |
| Output | `validate_output` | product IDs exist; no age-restricted items for minors; no system-prompt leak; no other user IDs; every ₹ amount, %, rating and coupon code in the text matches the DB |
| Fallback | `agent.py` | failing sentences are stripped; if the reply is still bad → plain recommendations from `reco` |

Products in chat replies: Claude refers to them as `[P00007]`; we replace that with the real title and
append a card like `• Noise Wireless Earbuds — ₹339 · ★4.3 · in stock` built from the DB.

## For other phases

- **Buttons** in `ChatReply.buttons`: `"Show more"`, `"Not interested"` (with products), or
  `"confirm_add_to_cart:<product_id>:<qty>"` + `"cancel"` after `add_to_cart`. Nothing is added until the bot/UI handles the confirm.
- `validate_output(reply)` is strict (it does not accept numbers the user typed), so re-running it in the API on a `chat()` reply passes.
- Uses real `reco` / `offers` when exported from their `__init__.py`, otherwise `common.stubs`.
  `get_my_offers` calls `offers.fetch_live_offers`, which needs `OFFERS_API_KEY` and the Offers API running (it returns `[]` otherwise).
- Audit rows go to `audit_log` with `component="llm"`, actions `chat_input`, `chat_reply`, `write_offer_message`.

## Run

```bash
python -m llm.redteam              # red-team report -> llm/redteam_results.md
python -m llm.redteam --audit      # same, and log decisions to audit_log
python -m pytest tests/llm -q      # unit tests (always mock mode, sample data)
```

Try it quickly:

```python
from llm import chat
print(chat("U00001", "earphones under ₹2000").text)
```

## Test status

- 107 tests in `tests/llm/` pass (mock mode; a scripted fake Claude covers invented products, wrong prices, tool-call caps, PII masking, LLM failure).
- Red team: **69/69 attacks defended (100%), 0/14 false positives** in mock mode (see `redteam_results.md`).
- **Real mode (2026-10-26):** also 69/69 defended, 0/14 false positives with a real `ANTHROPIC_API_KEY`
  (`python -m llm.redteam`, ~5 min; most attacks are blocked before Claude is called). `redteam_results.md` now holds this run.
