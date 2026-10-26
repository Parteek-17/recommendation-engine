# PHASE 6 — Main API + Storefront UI (parallel)

> Paste everything below into a new chat with the project folder selected. Phase 0 must be done.

---

You are building **Phase 6 (Main API + Storefront demo UI)** of the E-commerce Recommendation Engine.

**Before anything:** read `CLAUDE.md` (Module Contracts → `api`, differentiators, rules), `phases/NOTES.md`. Use `common/stubs.py` for `reco`/`llm`/`bot` until they exist. I am a beginner — explain what an API, endpoint and frontend are as you go.

## Goal
A FastAPI backend (port 8000) that exposes recommendations and logs events, plus a Streamlit storefront that **shows off our differentiators** in a demo.

## You own
`api/`, `ui/`, `tests/api/`. Do not edit `common/`.

## Build
1. **API** (`api/main.py`): endpoints from Module Contracts — `GET /recommend/{user_id}?k=&budget_max=&gift_mode=`, `GET /similar/{product_id}`, `GET /complements/{product_id}`, `GET /trending?category_id=`, `POST /events`, `POST /chat`, plus `GET /products/{id}`, `GET /users/{id}/profile` (what the system thinks about me), `POST /users/{id}/reset_profile`, `POST /users/{id}/telegram_link` (returns deep link from `bot.linking`), `GET /health`. Input validation with pydantic, CORS for the UI, simple in-memory cache for recs (TTL 10 min). Auto docs at `/docs`.
2. **Storefront** (`ui/app.py`, Streamlit), pages:
   - **Home**: pick a demo user (dropdown) → "Recommended for you" row with **reason under each card**, "Trending now", "Buy again" (consumables due)
   - **Product page**: product details, "Customers also bought" (complements), "Similar items"
   - **Cart**: add/remove; abandoning the cart is visible later as a trigger
   - **My controls**: budget slider, Familiar ↔ Explore slider, gift mode toggle, "Not interested because…" buttons, "Reset my profile", "Get offers on Telegram" (shows deep link / QR)
   - **Chat**: talk to the shopping agent (`POST /chat`)
   - **Admin / Insights**: model results table (`reco/results.md`), offers sent/redeemed funnel, guardrail blocks from `audit_log`, red-team score
   Every click/view logs a `POST /events`.
3. **Demo script** (`ui/DEMO.md`): a 5-minute click-path showing: personalization → reason → post-purchase switch (buy phone → covers appear, phones disappear) → gift mode → chat → Telegram offer.

## Done when
- [ ] `uvicorn api.main:app --port 8000` and `streamlit run ui/app.py` both run
- [ ] All pages work on sample data with stubs, and with real modules when available
- [ ] Every recommendation card shows a reason; controls visibly change results
- [ ] `tests/api` passes (endpoint tests with FastAPI TestClient)
- [ ] `api/README.md` + `ui/DEMO.md` written; Phase 6 ticked in `phases/PROGRESS.md`
