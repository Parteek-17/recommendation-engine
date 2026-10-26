# offers/ — Offer Engine + Offers API (Phase 3)

Our own engine that **creates** offers, a small web service (**Offers API**) that serves them, and all the
**offer guardrails** that decide whether an offer may be sent. In the Offer Pipeline (CLAUDE.md) this module
owns steps 2 (eligibility), 3 (fetch live offers), 5 (offer guardrails) and the re-validation in step 7.

> "LLM proposes, rules decide": the codes, values, products and expiry dates in an offer come from here
> (the DB), never from Claude. Claude only writes the wording.

## Files

| File | What it does |
|---|---|
| `rules.py` | Business rules in one place: discount sizing, margin floor, ₹ value of an offer, allowed segments, "do two offers stack?" |
| `store.py` | Database reads/writes (offers, sends, users, product catalog, price snapshots) |
| `segments.py` | Which segments a user is in (city tier, new/returning, budget band — **never** gender/religion) + list of live offers |
| `generator.py` | Rule-based offer creation per trigger type |
| `api.py` | FastAPI Offers API (port 8001), protected by `X-API-Key` |
| `client.py` | `fetch_live_offers()` — calls the API with timeout + retry, returns `[]` if it's down |
| `eligibility.py` | `check_eligibility()` — consent, Telegram linked, quiet hours, daily/weekly caps, segment |
| `guardrails.py` | `apply_offer_guardrails()` + `revalidate()` |
| `approve.py` | `python -m offers.approve` — admin approves/rejects high-value offers |
| `analysis.py` | `python -m offers.analysis` — uplift report → `offers/analysis.md` |

Contract functions are exported from `offers/__init__.py`:

```python
from offers import fetch_live_offers, check_eligibility, apply_offer_guardrails, revalidate
from offers.generator import generate_offers      # not part of the contract, import directly
```

## How the generator decides

| Trigger | Offer | Example |
|---|---|---|
| `cart_abandon` | `pct_off` on each item left in the cart (or `free_delivery` if its margin is too thin) | 10% off the sneakers you left |
| `price_drop` | `flat_off` (₹, rounded down to ₹10) on a wishlisted item | ₹120 off |
| `repurchase_due` | `buy_again` on a consumable due within 7 days | 7% off your atta |
| `post_purchase` | `bundle`: the item you bought + a discount on a **complement** (never a substitute) | phone → 10% off a cover |
| `win_back` | `pct_off` on the user's favourite category | 15% off Fitness |
| `festival` | category-wide `pct_off` for everyone (top categories), min cart ₹499 | 20% off Board Games |

**Discount size** = base % for the trigger × price-sensitivity multiplier
(`low` budget band 1.5×, `mid` 1.0×, `high` 0.7×), then limited by:

1. `MAX_DISCOUNT_PCT` from `.env`;
2. the **margin floor**: after the discount we keep at least 2 points of margin (`margin_pct − 2`).
   A phone with 5.3% margin can get at most 3%. Category offers use the *lowest* margin in the category.

Then: a unique code (`CART10-7KQ2`), an expiry (2–14 days depending on trigger), and
`requires_approval = 1` if the offer is worth more than `APPROVAL_THRESHOLD_INR`.
Personal offers get the segment `user:<user_id>`, so only that user ever sees them.
An identical offer that is already live is not created twice.

## Guardrails (what gets an offer removed)

`apply_offer_guardrails(user_id, ranked_offers)` keeps the order and removes offers that:

- are expired, not started yet, or inactive
- need admin approval and haven't got it
- use a sensitive or unknown segment (gender, religion, …), or the user isn't in the segment
- are above `MAX_DISCOUNT_PCT` or below a product's margin floor
- offer an age-restricted product (or a category containing one) to a minor
- were already sent to this user and not used yet (dedup)
- were already redeemed `max_uses_per_user` times
- would **stack**: overlap an offer the user still holds, or a better offer earlier in the list
  (whole-cart offers overlap everything; category/product offers overlap if their categories meet)
- would push today's total discount (all users) over `DAILY_DISCOUNT_BUDGET_INR`

Every removal is written to `audit_log` (`component='offers', action='offer_guardrail'`) with the reason.

`revalidate(offer_id, user_id)` runs right before sending: still active and approved, products in stock,
**price unchanged** since the offer was generated (from the `offer_price_snapshots` table), and the margin floor
still holds at today's price.

## Eligibility

`check_eligibility(user_id, trigger_type)` → `GuardrailResult` with the first failing reason:
not opted in → Telegram not linked → quiet hours (user's own `quiet_start/quiet_end`, else `.env`) →
daily cap (`MAX_OFFERS_PER_DAY`) → weekly cap (`MAX_OFFERS_PER_WEEK`, or the user's lower `max_offers_per_week`)
→ no live offer for this trigger in the user's segments. Blocked/failed sends don't count toward caps.

## How to run

You need `OFFERS_API_KEY` in `.env` (not `change-me` — the API refuses to run with the placeholder).
Make one with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Start the API:

```powershell
uvicorn offers.api:app --port 8001
```

Open <http://127.0.0.1:8001/docs>. Each protected route has an `x-api-key` field: paste your key there, then **Try it out**.

| Route | Key needed | What |
|---|---|---|
| `GET /health` | no | `{"status": "ok"}` |
| `GET /offers/live?user_id=&trigger_type=` | yes | active, unexpired offers (filtered to the user's segments) |
| `GET /offers/{offer_id}` | yes | one offer, 404 if unknown |
| `POST /offers/generate` | yes | body `{"trigger_type": "festival", "user_id": null, "save": true}` → new offers |

Other commands:

```powershell
python -m offers.generator festival --dry-run          # see what would be created
python -m offers.generator cart_abandon --user U00004  # create personal offers
python -m offers.approve                               # approve / reject high-value offers
python -m offers.analysis                              # write offers/analysis.md
```

## How to test

```powershell
pytest tests/offers        # 106 tests, ~45 s
```

Tests use a temp copy of the sample data and a fixed clock (2026-10-01 12:00), so they never touch
`data/app.db` and don't depend on today's date. Every guardrail has a test that proves it blocks the bad case
(`tests/offers/test_guardrails.py`); the client tests include a real "nothing listening on that port" case.

## Design notes

- **Price sensitivity** comes from the rec engine's behavioural `budget_band` (`reco.build_user_context`,
  stub until Phase 2 is done) — never from the hidden ground truth.
- **Free delivery** is valued at ₹49 and **points** at ₹0.50 each when checking budgets/approval (`rules.py`).
- **Bundle** discounts are taken off the add-on item. The guardrail lets the margin of both items pay for it
  (they are bought together); the generator is stricter and sizes on the add-on alone.
- **Uplift analysis** compares redemption rate by discount size × sensitivity with each group's organic 7-day
  purchase rate. With the sample data's 9 delivered sends it only demonstrates the method; re-run on the full
  synthetic data.
