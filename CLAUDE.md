# E-commerce Recommendation Engine — Project Context

> Every chat working on this project reads this file first. It is the **single source of truth**.
> If something here conflicts with a phase prompt, THIS file wins.

## Current Phase: BUILD (phase-by-phase, parallel chats)

- Planning is complete. We now build in phases; each phase runs in its **own chat** using the prompt in `phases/`.
- Order: **Phase 0 first (alone)** → **Phases 1–6 in parallel** → **Phase 7 last (integration)**. See `phases/README.md`.
- Beginners start with `docs/BEGINNER_GUIDE.md`. Setup / keys / connection problems → `docs/SETUP_GUIDE.md`.

## Rules for every phase chat (parallel safety)

1. **Only edit files inside your own module folder** (listed in your phase prompt) + your own tests in `tests/<module>/`.
2. **Do not edit `common/`** (owned by Phase 0). If you need a contract change, write it in `phases/NOTES.md` and work around it with a local helper.
3. New Python packages: **append** one line to `requirements.txt` (never reorder/delete lines).
4. If a module you depend on isn't built yet, use the **stub** in `common/stubs.py` — never block on another phase.
5. Stick to the **exact names** in the Data Contract and Module Contracts below.
6. Never hard-code secrets. Read them via `common/config.py` (which loads `.env`). Never print or log keys.
7. Keep code simple and beginner-readable: small functions, docstrings, comments explaining *why*.
8. When done: write `<module>/README.md` (what it does, how to run, how to test) and tick your phase in `phases/PROGRESS.md`.

## Decisions Log

- **Store domain (default, changeable):** Indian general marketplace (Electronics, Fashion, Home & Kitchen, Beauty, Grocery, Sports, Books, Toys). Prices in ₹.
- **Dataset:** Our OWN synthetic dataset with planted realistic patterns (hidden user preferences, long-tail popularity, complement/substitute relations, repurchase cycles, seasonality/sale spikes, purchase funnel, gift sessions, cold-start users/items, noise). Hidden ground truth saved separately for evaluation.
- **Standard Data Format:** the whole system reads ONE format (Data Contract below). Any source — our generator, Amazon/Myntra/RetailRocket public datasets, a store's CSV — is converted by an adapter (`ingest/`, YAML mapping per source) + validator. Works with partial data (levels L1–L4); missing features switch off gracefully. Spec: `docs/DATA_STANDARD.md`. `.env` `DATA_SOURCE=sample|synthetic|external`.
- **Rec architecture:** multi-stage — candidate generation (popularity, item-item CF, ALS, content similarity) → LightGBM ranker → re-ranking (business rules, diversity). Simple end-to-end first, then upgrade.
- **Differentiators:** post-purchase intelligence (no substitutes after purchase, complements instead; repurchase reminders), explainable recs (every rec has a reason), user control (budget, gift mode, "not interested because…"), budget-aware personalization.
- **Agentic AI + guardrails:** "LLM proposes, rules decide". Prices/stock/product IDs/offer values always come from DB, never from the LLM. Layers: input guardrails → tool/action guardrails (allowlisted tools, write actions need confirmation, no payments) → business rules → output grounding validation → fallback to non-LLM output.
- **Offers:** Created by our own **Offer Engine**, served by our own **Offers API** (FastAPI, protected by internal `OFFERS_API_KEY`). Rec engine matches + ranks offers. Claude writes message wording only.
- **Delivery channel:** Telegram bot (deep-link account linking, /start = opt-in, /stop = opt-out, inline buttons feed back into events).
- **LLM:** Claude API. Model names come from `.env` (`CLAUDE_MODEL_FAST` for offer messages, `CLAUDE_MODEL_SMART` for chat agent).
- **Storage:** SQLite (`data/app.db`) + CSV files. Simple, no server needed.
- **Secrets:** `.env` only (template `.env.example`). `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `OFFERS_API_KEY` (plus non-secret settings like `TELEGRAM_BOT_USERNAME`, `TIMEZONE=Asia/Kolkata`, guardrail limits). Never in code, git, logs, frontend, or LLM prompts.

## Offer Pipeline (APPROVED)

```
Trigger detected
   ↓
Eligibility check (consent, frequency cap, segment)
   ↓
Offers API → fetch live offers   ← OFFERS_API_KEY used only here, server-side
   ↓
Match + rank offers for this user (uses the rec engine)
   ↓
Offer guardrails (validate, caps, rules)
   ↓
LLM writes the message (wording only)   ← ANTHROPIC_API_KEY, server-side
   ↓
Final re-validation → Send (Telegram) → Track (opened / clicked / redeemed)
```

If the LLM fails → send a plain template message. If any guardrail fails → don't send, log reason in `audit_log`.

## Tech Stack

Python 3.11 · pandas · numpy · Faker · scikit-learn · scipy · implicit (ALS) · LightGBM · FastAPI + uvicorn · SQLite · python-telegram-bot · anthropic · pydantic · python-dotenv · APScheduler · Streamlit · pytest

## Folder Structure

```
ecommerce recommendation engine/
├── CLAUDE.md                ← this file
├── README.md                ← quick start
├── .env.example  .gitignore  requirements.txt
├── docs/                    ← BEGINNER_GUIDE.md, SETUP_GUIDE.md, DATA_PARAMETERS.md, DATA_STANDARD.md
├── phases/                  ← phase prompts, README.md, PROGRESS.md, NOTES.md
├── common/                  ← Phase 0: config.py, db.py, schemas.py, stubs.py, logging_utils.py
├── scripts/                 ← Phase 0: init_db.py, check_setup.py, make_sample_data.py (Phase 7 adds run_all)
├── data/
│   ├── sample/              ← Phase 0: tiny fixture CSVs (everyone can use from day 1)
│   ├── raw/                 ← Phase 1: full generated CSVs
│   ├── ground_truth/        ← Phase 1: hidden profiles + product relations
│   ├── external/            ← raw real-platform datasets (gitignored)
│   ├── processed/           ← external data converted to Standard Format
│   └── app.db               ← SQLite (created by scripts)
├── data_gen/                ← Phase 1
├── ingest/                  ← Phase 1: adapters, mappings/*.yaml, validator
├── reco/                    ← Phase 2
├── offers/                  ← Phase 3
├── llm/                     ← Phase 4
├── bot/                     ← Phase 5
├── api/  ui/                ← Phase 6
├── pipeline/                ← Phase 7 (scheduler, triggers, orchestration)
├── models/                  ← saved trained models (gitignored)
└── tests/<module>/
```

## Data Contract (exact table + column names)

IDs are strings: users `U00001`, products `P00001`, categories `C001`, sessions `S0000001`, events `E00000001`, orders `O0000001`, offers `OF0001`. Timestamps ISO-8601. Lists stored pipe-separated: `a|b|c`.

| Table | Columns |
|---|---|
| users | user_id, signup_date, age_group, gender, city, city_tier, device, is_minor |
| categories | category_id, name, parent_id |
| products | product_id, title, description, category_id, brand, price, mrp, margin_pct, avg_rating, rating_count, stock_qty, launch_date, tags, is_consumable, repurchase_days, age_restricted |
| sessions | session_id, user_id, start_time, device, is_gift |
| events | event_id, user_id, product_id, session_id, event_type, timestamp, source, search_query |
| orders | order_id, user_id, order_date, total_amount |
| order_items | order_id, product_id, quantity, price_paid |
| reviews | review_id, user_id, product_id, rating, review_text, review_date |
| offers | offer_id, code, offer_type, value, min_cart_value, category_id, product_id, bundle_product_id, segment, trigger_type, max_uses_per_user, start_at, expires_at, is_active, requires_approval |
| offer_rankings | user_id, offer_id, score, rank, created_at |
| offer_sends | send_id, user_id, offer_id, trigger_type, message, channel, status, sent_at, updated_at |
| user_consent | user_id, opted_in, quiet_start, quiet_end, preferred_categories, max_offers_per_week |
| telegram_links | user_id, chat_id, link_token, linked_at, active |
| audit_log | id, timestamp, component, user_id, action, decision, reason, details_json |
| id_map | entity, internal_id, source, external_id |

Ground truth (evaluation only — **never used as model input**): `user_hidden_profile` (user_id, preferred_categories, price_sensitivity, activity_level, is_gifter), `product_relations` (product_id, related_product_id, relation = complement|substitute).

Enums:
- event_type: `view, click, add_to_cart, remove_from_cart, wishlist, purchase, search`
- source: `home, search, product_page, recommendation, telegram`
- offer_type: `pct_off, flat_off, free_delivery, bundle, buy_again, points`
- trigger_type: `cart_abandon, price_drop, repurchase_due, post_purchase, win_back, festival`
- offer_sends.status: `queued, sent, opened, clicked, redeemed, blocked, failed`
- Event weights for training: view 1, click 2, wishlist 3, add_to_cart 4, purchase 6
- Data window: 2026-04-01 → 2026-09-30. Time split: train ≤ Aug 31, validation = Sep 1–15, test = Sep 16–30.

## Module Contracts (function signatures everyone codes against)

Shared models live in `common/schemas.py` (pydantic): `RecItem(product_id, score, reason, source)`, `Offer`, `RankedOffer(offer_id, score, reason)`, `UserContext(user_id, budget_band, top_categories, trigger_type, is_gift_mode)`, `ChatReply(text, product_ids, buttons)`, `GuardrailResult(allowed: bool, reason: str)`.

| Module | Functions / endpoints |
|---|---|
| `reco` | `get_recommendations(user_id, k=10, context=None) -> list[RecItem]` · `get_similar(product_id, k=10) -> list[RecItem]` · `get_complements(product_id, k=10) -> list[RecItem]` · `get_trending(k=10, category_id=None) -> list[RecItem]` · `rank_offers_for_user(user_id, offers) -> list[RankedOffer]` · `build_user_context(user_id) -> UserContext` |
| `offers` | Offers API: `GET /offers/live` (header `X-API-Key`) · `GET /offers/{offer_id}` · `POST /offers/generate` · Python: `fetch_live_offers(user_id=None) -> list[Offer]` · `check_eligibility(user_id, trigger_type) -> GuardrailResult` · `apply_offer_guardrails(user_id, ranked_offers) -> list[RankedOffer]` · `revalidate(offer_id, user_id) -> GuardrailResult` |
| `llm` | `write_offer_message(offer, user_context) -> str` · `chat(user_id, message) -> ChatReply` · `check_input(text) -> GuardrailResult` · `validate_output(reply) -> GuardrailResult` |
| `bot` | `create_link_token(user_id) -> str` (deep link) · `send_offer(user_id, message, offer_id) -> bool` · `send_text(user_id, text, buttons=None) -> bool` · button callbacks log events with source=`telegram` |
| `api` | `GET /recommend/{user_id}` · `GET /similar/{product_id}` · `GET /complements/{product_id}` · `GET /trending` · `POST /events` · `POST /chat` |
| `pipeline` | `detect_triggers(now) -> list[(user_id, trigger_type)]` · `run_offer_pipeline(user_id, trigger_type) -> str` (status) · scheduler every 15 min |

## Open Questions

- Team size and timeline (assumed ~8 weeks; adjust in `phases/README.md`).
