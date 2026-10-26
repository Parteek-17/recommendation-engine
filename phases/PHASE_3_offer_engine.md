# PHASE 3 — Offer Engine + Offers API (parallel)

> Paste everything below into a new chat with the project folder selected. Phase 0 must be done.

---

You are building **Phase 3 (Offer Engine + Offers API)** of the E-commerce Recommendation Engine.

**Before anything:** read `CLAUDE.md` (Offer Pipeline, Module Contracts → `offers`, Data Contract, rules), `phases/NOTES.md`. Use `common/stubs.py` for `reco` until Phase 2 is done. I am a beginner — explain each step simply.

## Goal
Our own engine that **creates** offers, an **Offers API** that serves live offers (protected by `OFFERS_API_KEY`), and all **offer guardrails** (steps 2, 3, 5 and 7-revalidation of the Offer Pipeline).

## You own
`offers/`, `tests/offers/`. Do not edit `common/`.

## Build
1. **Offer generator** (`offers/generator.py`): rule-based creation of offers per trigger type:
   - cart_abandon → pct_off / free_delivery on cart items
   - price_drop → flat_off on wishlisted item
   - repurchase_due → buy_again on consumables
   - post_purchase → bundle on complements
   - win_back → pct_off on user's favourite category
   - festival → category-wide pct_off
   Discount sizing: never below the product's margin floor (`margin_pct`), bigger for price-sensitive segments, capped by `MAX_DISCOUNT_PCT`. Unique codes, expiry, `requires_approval` when value > `APPROVAL_THRESHOLD_INR`.
2. **Offers API** (`offers/api.py`, FastAPI on port 8001): `GET /offers/live` (optional `?user_id=` / `?trigger_type=`), `GET /offers/{offer_id}`, `POST /offers/generate`, `GET /health`. Every route except `/health` requires header `X-API-Key == OFFERS_API_KEY` → else 401. Auto docs at `/docs`.
3. **Client** (`offers/client.py`): `fetch_live_offers(user_id=None)` calls the API with the key from config (server-side only), with timeout + retry with backoff; on failure returns `[]` (never fake offers) and writes `audit_log`.
4. **Eligibility** (`offers/eligibility.py`): `check_eligibility(user_id, trigger_type)` → consent opted_in + Telegram linked, not in quiet hours, under `MAX_OFFERS_PER_DAY` / `MAX_OFFERS_PER_WEEK`, segment match. Returns `GuardrailResult` with clear reason.
5. **Offer guardrails** (`offers/guardrails.py`): `apply_offer_guardrails(user_id, ranked_offers)` removes offers that are: expired/inactive, already sent to this user (dedup), over `max_uses_per_user`, would stack with another active offer, exceed daily discount budget, target a minor with age-restricted product, need approval but not approved. Segment rules must never use gender/religion/sensitive attributes. Log every removal in `audit_log`.
6. **Re-validation** (`offers/guardrails.py`): `revalidate(offer_id, user_id)` right before sending → still active, product in stock, price unchanged.
7. **Admin approval** (simple): `python -m offers.approve` lists pending high-value offers and approves/rejects them.
8. **Uplift analysis** (`offers/analysis.py`): using `offer_sends` history, redemption rate by discount size × price sensitivity → shows "don't over-discount users who'd buy anyway". Writes `offers/analysis.md`.

## Done when
- [ ] `uvicorn offers.api:app --port 8001` runs; wrong/missing key → 401; `/docs` works
- [ ] Generator produces valid offers for all 6 trigger types; no offer below margin floor or above cap
- [ ] Each guardrail has a unit test that proves it blocks the bad case
- [ ] API down → client returns `[]` and logs, no crash
- [ ] `offers/README.md` written; Phase 3 ticked in `phases/PROGRESS.md`
