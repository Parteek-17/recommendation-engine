# PHASE 5 — Telegram Bot (parallel)

> Paste everything below into a new chat with the project folder selected. Phase 0 must be done.

---

You are building **Phase 5 (Telegram bot)** of the E-commerce Recommendation Engine.

**Before anything:** read `CLAUDE.md` (Delivery channel decision, Module Contracts → `bot`, rules), `phases/NOTES.md`, and the Telegram section of `docs/SETUP_GUIDE.md`. Use `common/stubs.py` for `llm`/`reco`/`offers` until they exist. I am a beginner — explain how Telegram bots, polling and callbacks work.

## Goal
A Telegram bot that links to our users, sends offers safely (rate-limited queue), lets users chat with the shopping agent, and logs every interaction back as events.

## You own
`bot/`, `tests/bot/`. Do not edit `common/`.

## Build (python-telegram-bot, async)
1. **Account linking** (`bot/linking.py`): `create_link_token(user_id)` → deep link `https://t.me/<BotName>?start=<token>`; `/start <token>` saves `telegram_links` (user_id ↔ chat_id) and sets `user_consent.opted_in=1`. `/start` without token → explain how to link. Tokens single-use, expire in 24 h.
2. **Commands** (`bot/handlers.py`):
   - `/start` link + opt-in · `/stop` opt-out (stop all offers immediately, confirm to user)
   - `/offers` my active offers · `/recommend` top picks with reasons
   - `/settings` quiet hours, max offers/week, favourite categories (inline buttons)
   - `/gift` toggle gift mode · `/help`
3. **Chat mode**: any free text → `llm.chat(user_id, text)` → reply with product cards. Unlinked users get a polite "please link first".
4. **Inline buttons** on offers/products: 🛒 View · 🎟 Apply offer · 👎 Not interested (follow-up: already bought / too expensive / not my style). Each click → write to `events` (source=`telegram`) and update `offer_sends.status` (opened/clicked/redeemed). "Not interested" reasons stored for the reco engine.
5. **Sending API** (`bot/sender.py`): `send_offer(user_id, message, offer_id)` and `send_text(user_id, text, buttons=None)`. Go through a **queue** respecting Telegram limits (~1 msg/s per chat, ~30 msg/s overall); handle `Forbidden` (user blocked bot → mark link inactive + opted_in=0), `RetryAfter` (wait), network errors (retry then `failed`). Final check before every send: still opted in, not quiet hours.
6. **Run modes**: `python -m bot.main` with long polling (development). Document webhook option for deployment in README (no need to implement fully).
7. **Safety**: bot token only from config; never echo internal errors to users; rate-limit chat messages per user (e.g. 10/min); all user text passes through `llm.check_input` (via the agent).

## Done when
- [ ] Bot runs with a real token; `/start <token>` links a test user; `/stop` opts out
- [ ] `send_offer` delivers a message with working buttons; clicks appear in `events` and `offer_sends`
- [ ] Blocked-bot and rate-limit cases handled (unit tests with mocked Telegram)
- [ ] Works with stubs when other modules are missing
- [ ] `bot/README.md` written; Phase 5 ticked in `phases/PROGRESS.md`
