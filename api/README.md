# api/ — Main API (Phase 6)

## What is this?

An **API** is a program that answers requests over HTTP. The storefront (or the bot, or
`curl`) asks a question like `GET /recommend/U00001` and gets **JSON** back.
Each *URL + method* (`GET`, `POST`, `PUT`) is called an **endpoint**.

This API is the single door to the recommendation system. It:

1. checks the input (pydantic → clear `422` error if something is wrong),
2. calls the other modules — `reco`, `llm`, `bot`, `pipeline` — or their **stubs** if they
   aren't built yet (`api/modules.py`; `/health` shows which is which),
3. applies the **user's own controls** (budget, gift mode, explore, "not interested"),
   and the **post-purchase switch** (`api/personalize.py`),
4. adds product details (title, price, …) so the UI can draw cards.

## Run it

```bash
uvicorn api.main:app --port 8010 --reload
```

Then open **http://127.0.0.1:8010/docs** — FastAPI builds interactive docs where you can
try every endpoint with a "Try it out" button.

## Endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Up? Data level? Which modules are real vs stub |
| GET | `/recommend/{user_id}?k=&budget_max=&gift_mode=&explore=` | Personal recs, each with a `reason` |
| GET | `/similar/{product_id}` | Alternatives (substitutes) |
| GET | `/complements/{product_id}` | "Customers also bought" |
| GET | `/trending?category_id=` | Popular now |
| GET | `/users/{id}/buy_again` | Consumables due for a repurchase |
| GET | `/products?q=&category_id=` · `/products/{id}` · `/categories` | Catalog |
| POST | `/events` | Log a view / click / cart / wishlist / search event |
| POST | `/sessions` | Start a shopping session (`is_gift` for gift sessions) |
| POST | `/orders` | Checkout: order + items + `purchase` events (prices from the DB) |
| POST | `/chat` | Shopping agent; product IDs are grounding-checked |
| GET | `/users` · `/users/{id}/profile` | Demo users · "what the system thinks about me" |
| GET/PUT | `/users/{id}/prefs` | Budget, explore, gift mode |
| POST | `/users/{id}/not_interested` | `reason` = `too_expensive` / `already_have` / `not_my_style` / `not_relevant` |
| POST | `/users/{id}/reset_profile` | Forget learned taste + controls |
| POST | `/users/{id}/telegram_link` | Deep link to the bot (from `bot.linking`) |
| GET | `/admin/insights` | Model results, offer funnel, guardrail blocks, red-team score |
| GET | `/admin/offer_sends?status=&trigger_type=&user_id=&limit=` | Every offer send, newest first, with code, message and status (Admin → Sent offers table) |
| GET | `/admin/triggers?now=` · POST `/admin/run_offer` | Detect triggers · run the offer pipeline for a user |

## How recommendations are finished (`personalize.py`)

```
reco candidates (k×4) → post-purchase switch → user controls → explore mix → top-up with trending
```

- **Post-purchase:** recent non-consumable purchases → their complements go first ("Goes with the
  … you just bought"), and other products of the same category (substitutes) are removed.
- **Controls:** hidden products/brands/categories, budget, age-restricted items for minors, out of stock.
- **Explore slider:** up to half of the slots go to popular items from categories the user doesn't browse.
- **Cache:** 10 minutes per request; cleared for a user on any event, order or control change.

User controls live in `data/user_prefs.json` (no contract table for them — see `phases/NOTES.md`).

## Test it

```bash
pytest tests/api tests/ui
```

Tests use a temporary copy of the sample data, so `data/app.db` is never changed.
