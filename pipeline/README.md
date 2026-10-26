# pipeline/ — Integration: triggers, the Offer Pipeline, the scheduler (Phase 7)

This is the glue. Every other module does one job; the pipeline decides **when** to do
them and **in which order**, so a shopper who left socks in their cart gets one well-chosen,
safe offer on Telegram — or nothing, with the reason written down.

## How the pieces connect

```
                 every 15 min (scheduler.py)
                          │
            detect_triggers(now)  (triggers.py)        "U00042 left socks in the cart 3 h ago"
                          │
       only users who are opted in + linked to Telegram, not handled in the last 24 h
                          │
            run_offer_pipeline(user, trigger)  (offer_pipeline.py)
                          │
 0  prepare      offers.generator   Offer Engine makes the personal offer (10% off THOSE socks)
 1  eligibility  offers             consent · Telegram linked · quiet hours · daily/weekly cap · segment
 2  fetch        offers.client  ──HTTP + X-API-Key──▶  Offers API :8001  (key never leaves the server)
 3  rank         reco               which live offer fits this user best ("For the socks in your cart")
 4  guardrails   offers             expired? too big? stacking? minor + 18+ item? daily ₹ budget?
 5  write        llm                Claude writes the WORDS; code, %, price, expiry come from the DB
                                    (Claude down → plain template, the pipeline does NOT stop)
 6  revalidate   offers             still live, still in stock, price unchanged since step 0
 7  send         bot                Telegram message with View / Apply / Not interested buttons;
                                    the bot writes the offer_sends row (queued → sent)
                          │
   user taps a button → bot → offer_sends.status = opened / clicked / redeemed + an events row
```

Every step writes one `audit_log` row (`component='pipeline'`, `action='step_<name>'`) and every run
ends with `action='run_offer_pipeline'` whose `decision` is the final status. So "why didn't U00042
get anything?" is always one SQL query away:

```sql
SELECT timestamp, action, decision, reason FROM audit_log
WHERE user_id = 'U00042' AND component IN ('pipeline', 'offers', 'bot', 'llm') ORDER BY id;
```

## Files

| File | What it does |
|---|---|
| `triggers.py` | `detect_triggers(now)` — the 6 trigger types (table below), most urgent first |
| `offer_pipeline.py` | `run_offer_pipeline(user_id, trigger_type)` → status; `run_pipeline(...)` → full result (offer, message, steps) |
| `scheduler.py` | APScheduler: offer round every 15 min + nightly `reco.train` at 03:00; `--dry-run`, `--once` |
| `store.py` | two pipeline-owned tables in app.db: `price_history` (for price drops) and `pipeline_runs` (24 h cooldown) |
| `show_triggers.py` | `python -m pipeline.show_triggers [--now ...]` — counts per trigger, read-only |
| `evaluate_offers.py` | offline evaluation of offer strategies + guardrails on a temp copy of the DB → `offer_eval.md` |

## Triggers

| Trigger | Fires when | Knob (`triggers.py`) |
|---|---|---|
| `cart_abandon` | added to cart > 2 h ago (last 3 days), not bought or removed, no newer add-to-cart | `CART_IDLE_HOURS`, `CART_LOOKBACK_DAYS` |
| `price_drop` | a wishlisted item (last 60 days, not bought) now costs ≥ 1% less than when wishlisted | `WISHLIST_LOOKBACK_DAYS`, `PRICE_DROP_MIN_PCT` |
| `repurchase_due` | consumable: last purchase + `repurchase_days` is between 7 days ago and 2 days ahead | `REPURCHASE_*_DAYS` |
| `post_purchase` | a purchase in the last 3 days of a product that has complements (`reco.get_complements`) | `POST_PURCHASE_DAYS` |
| `win_back` | a past buyer has had no activity for 30–90 days | `WIN_BACK_DAYS`, `WIN_BACK_MAX_DAYS` |
| `festival` | today is inside a `FESTIVALS` window (e.g. Diwali 20 Oct – 15 Nov 2026) → every opted-in user | `FESTIVALS` |

`price_drop` needs to know old prices. The contract `products` table only has today's price, so the
scheduler copies every changed price into `price_history` each round. It starts working once a price
actually changes after the scheduler first ran.

A user often has several triggers; the daily cap (1 offer/day) means only the most urgent one
(cart abandon first, festival last) gets used.

## Run it

```powershell
python -m pipeline.show_triggers                       # who has a trigger right now? (sends nothing)
python -m pipeline.scheduler --once --dry-run          # one round, prints the messages it WOULD send
python -m pipeline.scheduler --dry-run                 # every 15 min, never sends
python -m pipeline.scheduler                           # LIVE (needs the Offers API + bot running)
```

From Python:

```python
from pipeline import detect_triggers, run_offer_pipeline, run_pipeline
run_offer_pipeline("U00010", "cart_abandon")           # 'sent', 'blocked', 'no_offer', ...
r = run_pipeline("U00010", "cart_abandon", dry_run=True); print(r.status, r.offer_id, r.message)
```

The whole system starts with one command — see `scripts/run_all.ps1` / `scripts/run_all.sh`
(top-level README).

### Final statuses

| Status | Meaning |
|---|---|
| `sent` | delivered to Telegram |
| `dry_run` | would have been sent (dry-run mode) |
| `blocked` | eligibility said no (not opted in, not linked, quiet hours, cap, no offer for the segment) — or the bot's own last check did |
| `offers_api_down` | the Offers API didn't answer (after 3 tries) — nothing sent |
| `no_offer` | no live offer for this trigger |
| `guardrail_blocked` | every candidate offer was removed by the offer guardrails |
| `revalidate_failed` | the chosen offer changed (price/stock/expiry) right before sending |
| `failed` | Telegram didn't accept it (e.g. no bot token) |
| `error` | a bug somewhere — caught, audited, the scheduler keeps going |

## Dry run vs live

Dry run runs steps 0–6 for real and stops before step 7. Step 0 may still create offers in the
`offers` table (they're needed to rank and check them; identical live offers are never duplicated),
but nothing is sent, no `offer_sends` row is written, and the 24 h cooldown is not used.

`--now` (with `--once`) is for what-if *detection*. The Offers API always uses the real clock, so
offers generated for a time in the past are not "live" and such runs end in `no_offer`.

## Tests

```powershell
pytest tests/integration        # 21 tests, ~45 s
```

Real modules end to end; only Telegram (a fake bot) and the HTTP hop to the Offers API (straight into
the FastAPI app) are faked:

- new user links Telegram → abandons cart → pipeline sends a personal offer → taps **Apply offer** → `redeemed`
- opted-out user gets nothing (and no coupon is even created) · quiet hours respected · daily cap
- Offers API down → no send + audit entry · LLM down → template message sent
- minors never get an 18+ coupon; the 50% offer is removed by guardrails · dry run sends nothing
- a crash inside a module is contained (`error`) · each of the 6 trigger types · scheduler cooldown
- **no production module still uses `common.stubs`**

## Design choices

- **Step 0 before eligibility's segment check.** `check_eligibility` also asks "is there a live offer for this
  user's segment?". Personal offers (segment `user:<id>`) only exist after the generator runs, so the generator
  runs once the user has passed consent / Telegram / quiet hours / caps — never for opted-out users.
- **Scheduler pre-filter.** Most detected triggers belong to users who never linked Telegram. Running the full
  pipeline for each would only produce thousands of "not linked" audit rows every 15 minutes, so the scheduler
  skips them and records ONE summary row (`scheduler_round`) with the counts.
- **One offer per run.** Guardrails return all safe offers best-first; we send only `[0]`.
- **The bot owns `offer_sends`.** `bot.send_offer` writes the row and makes the final opt-in / quiet-hours check.

## Speed (profiled 2026-10-26/27)

One `run_offer_pipeline("U00010", "cart_abandon")`, Offers API on :8001, real Claude (`CLAUDE_MODEL_FAST`),
dry run (nothing sent). "Run" = time inside `run_pipeline`; Python start-up (`import pipeline`, 15–20 s on
this laptop) is extra and paid once per process. All "after" numbers were taken while another session was
training the rec engine (CPU at ~100 %), so an idle machine is faster still.

| Step | Before | After | What it was / what changed |
|---|---|---|---|
| eligibility → fetch | 14 s | 0.5 s | First `/offers/live` call loads the rec engine inside the API (5 s idle, >10 s busy). The client's 10 s read timeout fired, then it **retried**, so the server did the slow work twice. Read timeout is now 30 s and a read timeout is not retried (`offers/client.py`); the scheduler also warms the API once per round (`warm_up`). |
| rank | 4 s | 0.4–1 s | Mostly CPU contention; no code change. |
| guardrails | 7 s | 2.7 s | Mostly per-user history loads in `reco` (see follow-up below); no code change here. |
| LLM wording (`write_offer_message`) | 54 s (16–145 s in my runs) | 1.7–2.6 s | The Claude call itself is 1–4 s (407 tokens in, ~57 out). The rest was **`import anthropic` + creating the SDK client (HTTPS setup), ~14 s idle, far more under load**, all counted in this step. Now `pipeline` creates the client in a background thread while steps 0–4 run (`llm.client.warm_up`). Each offer request also has its own budget: 10 s timeout, 1 retry, `max_tokens` 300 (was 20 s × 3 attempts, 1024) — a stuck call now falls back to the template in ≤ ~21 s instead of ~60 s+. |
| **whole run, scheduler** (`warm_up()` done first) | ~90 s | **10.9 s** | |
| **whole run, cold process** (first run, e.g. demo CLI) | 28 s idle · 160 s under load | **19.6 s** under load | Remaining cold cost = waiting for the client warm-up thread. |

Re-measure: `python -X importtime` for imports; for steps, wrap the functions in `pipeline.offer_pipeline`
with a timer and call `run_pipeline(user, trigger, now=<midday>, dry_run=True)`.

**Follow-up (outside `pipeline/`):** `reco.data.load_user_history` calls `common.db.table_counts()`, which runs
`COUNT(*)` on every table (1.04 M events) just to ask "are there any events?" — ~0.6 s per call, ~5 calls per
run (offer generator, segments, guardrails, `build_user_context`). `SELECT 1 FROM events LIMIT 1` would save
~3 s per run.
