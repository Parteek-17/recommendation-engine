# bot/ — Telegram bot (Phase 5)

Links shop users to Telegram, sends offers safely, lets users chat with the shopping
assistant, and writes every button tap back into our data.

## How a Telegram bot works (beginner version)

- **Bot token** — the bot's password from @BotFather. Lives only in `.env` (`TELEGRAM_BOT_TOKEN`).
- **Long polling** — `python -m bot.main` keeps asking Telegram "anything new?". Each answer is an
  *update* (a message like `/start abc` or a button tap). python-telegram-bot calls the matching
  function in `handlers.py`. No public URL needed, so it works on a laptop.
- **Inline buttons + callbacks** — every button carries a hidden string (`callback_data`, max 64 bytes,
  e.g. `o:a:SN000012` = "Apply offer on send SN000012"). Tapping it sends that string back to us as a
  *callback query*; `handlers.on_button` handles it.
- **A bot can only message people who pressed Start.** If they block the bot, Telegram answers
  `Forbidden` and we stop messaging them.

## Files

| File | What it does |
|---|---|
| `linking.py` | `create_link_token(user_id)` → deep link `https://t.me/<bot>?start=<token>`; `link_chat(token, chat_id)` |
| `sender.py` | `send_offer`, `send_text`, the rate-limited `SendQueue`, `RateLimiter`, `ChatRateLimiter` |
| `handlers.py` | commands, free-text chat, button taps, error handler |
| `keyboards.py` | inline buttons + product/offer card text |
| `store.py` | small SQLite helpers (links, consent, events, offer_sends, gift mode) |
| `deps.py` | imports `llm` / `reco` / `offers` — real module if it exports the function, else `common.stubs` |
| `main.py` | builds the app and starts long polling |

## Run it

1. Fill `TELEGRAM_BOT_TOKEN` and `TELEGRAM_BOT_USERNAME` in `.env` (docs/SETUP_GUIDE.md, Step 7).
2. Make sure the DB exists: `python scripts/init_db.py`
3. Start the bot:
   ```
   python -m bot.main
   ```
4. Link a test user (another terminal):
   ```
   python -c "from bot import create_link_token; print(create_link_token('U00010'))"
   ```
   Open the printed link on your phone → **Start**. The bot says "Your account is linked".
5. Send an offer to that user:
   ```
   python -c "from bot import send_offer; print(send_offer('U00010', 'Hi! 10% off for you', 'OF0001'))"
   ```
   Tap the buttons, then look at `events` (source=`telegram`) and `offer_sends` in `data/app.db`.

Only one copy of a bot can poll at a time — each teammate should use their own test bot.

## What users can do

| Command | Effect |
|---|---|
| `/start <token>` | links the account (token from the website) and opts in |
| `/start` | already linked → opts back in; not linked → explains how to link |
| `/stop` | opts out immediately (`user_consent.opted_in = 0`) |
| `/offers` | offers we sent them that are still valid, with buttons |
| `/recommend` | 5 top picks with reasons (gift ideas when gift mode is on) |
| `/settings` | quiet hours, max offers per week (≤ system limit), favourite categories |
| `/gift` | toggles gift mode |
| `/help` | command list |
| any text | goes to `llm.chat(user_id, text)` → reply + up to 3 product cards |

## Buttons → data

| Button | `offer_sends.status` | `events` row (source=`telegram`) |
|---|---|---|
| 🛒 View (offer) | `clicked` | `click` on the offer's product (if it has one) |
| 🎟 Apply offer | `redeemed` (after `offers.revalidate`) | `add_to_cart` on the offer's product |
| 👎 Not interested (offer) | `opened` | — then asks why |
| 🛒 View (product) | — | `click` |
| Already bought / Too expensive / Not my style | — | stored in `audit_log` (see below) |

Statuses only move forward (`sent → opened → clicked → redeemed`). Each user can only use buttons
on offers sent to **them**. Clicks reuse a Telegram session in `sessions` (device=`telegram`) if
the last click was under 30 minutes ago.

**"Not interested" reasons** — there is no feedback table in the Data Contract yet, so they are written to
`audit_log` with `component='bot'`, `action='not_interested'`, `decision` = `already_bought` |
`too_expensive` | `not_my_style`, `details_json` = `{"product_id": ..., "offer_id": ...}`.

## Sending (`send_offer`, `send_text`)

```
send_offer(user_id, message, offer_id) -> bool
```
1. Inserts an `offer_sends` row (`queued`). **The pipeline should not insert its own row.**
2. Final check: active Telegram link, `opted_in = 1`, not in quiet hours (user's own, else `.env`).
   Fails → status `blocked` + audit reason, returns False.
3. Puts the message on the **SendQueue**: one background thread with its own `telegram.Bot`. It waits
   so we send at most ~1 msg/s per chat and 30 msg/s overall.
   - `Forbidden` (user blocked the bot) → link set `active = 0`, `opted_in = 0`, status `failed`
   - `RetryAfter` → waits the seconds Telegram asks (gives up if > 60 s or it happens > 3 times)
   - network error / timeout → retries with 2 s, 4 s waits, then `failed`
   - `BadRequest` (e.g. chat not found) → `failed`, no retry
4. Sent → status `sent`, `sent_at` set, audit row. Returns True.

Messages are sent as **plain text** (no Markdown/HTML), so text from the LLM can't inject links or
break formatting. Prices/titles in cards always come from the DB.

`send_text(user_id, text, buttons=None)` does the same checks. `buttons` are quick replies: tapping
one acts as if the user typed that text ("Show more" → `/recommend`).

Without `TELEGRAM_BOT_TOKEN` both return False (status `failed`) instead of crashing.

## Safety

- Token only from `common.config` and never logged; chat IDs are not written to `audit_log`.
- Users never see internal errors (`on_error` logs it and replies "Sorry, something went wrong").
- Chat: max 10 messages per user per minute; unlinked users don't reach the LLM.
- All free text goes through `llm.chat`, which runs `llm.check_input` (the stub does too).
- Link tokens: random, single-use, expire after 24 h (issue time is inside the token; the whole token
  must match the DB, so editing it doesn't help).

## Deploying with a webhook (not implemented)

Polling is fine for development. In production Telegram can *push* updates to a public HTTPS URL instead:

```python
app = build_application(token)
app.run_webhook(listen="0.0.0.0", port=8443, url_path="telegram",
                webhook_url="https://your-domain.example/telegram",
                secret_token="<random string from .env>")
```
Needs `pip install "python-telegram-bot[webhooks]"`, a public HTTPS address (or a reverse proxy such as
nginx / a tunnel), and a `secret_token` so only Telegram can call the URL. Only run polling **or** a
webhook, never both.

## Tests

```
python -m pytest tests/bot -q
```
Telegram is fully mocked (`tests/bot/conftest.py`: `FakeBot`, fake updates, fake clock/sleep). Covers
linking (single-use, expiry, re-linking), rate limits, `Forbidden` / `RetryAfter` / network errors,
opt-out / quiet hours / not-linked blocks, every button, settings, chat rate limit and error hiding.

## Works with stubs

`deps.py` uses `common.stubs` for `llm.chat`, `reco.get_recommendations` / `get_complements` /
`build_user_context`, and `offers.revalidate` until those modules export them from their `__init__.py`.
