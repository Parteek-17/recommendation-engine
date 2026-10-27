# Evaluation report — E-commerce Recommendation Engine (Parvaaz)

_Phase 7 · 2026-10-23, updated 2026-10-27 (reco re-evaluated, live Telegram + real Claude) · full synthetic data (`DATA_SOURCE=synthetic`, seed 42) unless stated otherwise._

Every number below comes from a script you can re-run:

| Section | Command | Raw output |
|---|---|---|
| Recommendations | `python -m reco.evaluate` | `reco/results.md` |
| Post-purchase & cold start | `python -m pipeline.report_examples` | `pipeline/report_examples.md` |
| Offer strategies, guardrails, funnel | `python -m pipeline.evaluate_offers` | `pipeline/offer_eval.md` |
| LLM red team | `python -m llm.redteam` | `llm/redteam_results.md` |
| Integration tests | `pytest tests/integration` | — |

---

## 1. Architecture

```
                          ┌──────────────────────────── data ────────────────────────────┐
  data_gen/ (synthetic) ──┤                                                              │
  ingest/ (Amazon,        ├─▶ Standard Format CSVs ─▶ SQLite data/app.db (15 contract tables)
  RetailRocket, Myntra)   │        (validator, levels L1–L4: missing data switches features off)
                          └──────────────────────────────────────────────────────────────┘
                                                    │
        ┌───────────────────────────────────────────┼──────────────────────────────────────────┐
        ▼                                           ▼                                          ▼
  reco/  (Phase 2)                          offers/ (Phase 3)                           llm/ (Phase 4)
  popularity · item-CF · ALS · TF-IDF       Offer Engine (rule-based generator)          Claude: offer wording +
  → ~200 candidates → LightGBM ranker       Offers API :8001  (X-API-Key)                chat agent (8 allowlisted tools)
  → re-ranking rules → top-k + reason       eligibility · guardrails · revalidate        input/output guardrails, fallbacks
        │                                           │                                          │
        └───────────────┬───────────────────────────┴────────────────────┬─────────────────────┘
                        ▼                                                ▼
              api/ Main API :8000  ◀── ui/ Storefront :8501       pipeline/ (Phase 7)
              recs, events, orders, chat,                          scheduler (15 min) → detect_triggers
              user controls, admin                                 → run_offer_pipeline (8 steps)
                                                                           │
                                                                           ▼
                                                                  bot/ Telegram (Phase 5)
                                                                  link · opt-in/out · offers · buttons
                                                                           │ taps → events / offer_sends
                                                                           ▼
                                                                    audit_log (every decision)
```

**The Offer Pipeline** (CLAUDE.md, implemented in `pipeline/offer_pipeline.py`, one `audit_log` row per step):

```
trigger ─▶ 0 prepare (Offer Engine makes the personal offer) ─▶ 1 eligibility ─▶ 2 fetch (Offers API, key server-side)
        ─▶ 3 rank (reco) ─▶ 4 offer guardrails ─▶ 5 LLM writes words only (fails → template) ─▶ 6 revalidate ─▶ 7 Telegram
```

"LLM proposes, rules decide": codes, prices, discounts, expiry dates and product IDs only ever come from the DB.
Any "no" stops the run with the reason in `audit_log`; an LLM failure falls back to a template instead of stopping.

## 2. Dataset

Our own synthetic Indian marketplace (`python -m data_gen.generate --size full`), data level **L4** (all features on).
Window 2026-04-01 → 2026-09-30; train ≤ Aug 31, validation Sep 1–15, test Sep 16–30.

| Table | Rows | | Table | Rows |
|---|---|---|---|---|
| users | 10,000 (519 minors, 1,000 signed up in Sept) | | orders / order_items | 29,678 / 40,464 (avg ₹5,188) |
| products | 3,000 in 8 parent + 25 sub-categories (₹39 – ₹79,809; 833 consumables, 37 age-restricted, 298 launched after Aug 20) | | reviews | 1,922 |
| sessions | 109,782 (767 gift sessions) | | offers | 54 starter offers (incl. 4 guardrail test offers) |
| events | **1,038,441** | | offer_sends | 13,224 simulated (Jul–Sep) |
| user_consent | 10,000 (5,631 opted in) | | ground truth | 2,198 complement + 39,787 substitute pairs, hidden taste per user |

| Event type | Count | | Event source | Count |
|---|---|---|---|---|
| view | 600,955 | | product_page | 643,389 |
| click | 252,406 | | home | 234,290 |
| add_to_cart | 92,094 | | recommendation | 119,917 |
| purchase | 40,464 | | search | 38,674 |
| wishlist | 28,998 | | telegram | 2,171 |
| search | 12,018 | | | |
| remove_from_cart | 11,506 | | | |

Planted patterns the models must find (hidden ground truth, never a model input): 2–3 favourite categories per
user (80% of browsing), a price comfort zone, Zipf long tail, post-purchase behaviour (substitute interest ×0.1 for
60 days, complements ×3 for 14 days), repurchase cycles, weekend/evening peaks + an Aug 10–15 sale, gift sessions,
cold-start users/items, and an offer response model with a 20% holdout for uplift (`data_gen/README.md`).

**External data** (`DATA_SOURCE=external`): smoke-tested on the RetailRocket-format fixture
(`ingest.run --mapping retailrocket.yaml`): detected as level **L1** (events only), content model, ranker and
offers switched off automatically, item-CF/ALS still recommend, `detect_triggers` returns nothing instead of
crashing. Reasons fall back to product IDs because L1 has no titles. A run on a real downloaded dataset is still to do (§8).

## 3. Recommendation results

Backtest on the untouched **test window (Sep 16–30)**, 1,000 sampled users, models trained on everything before it.
"Relevant" = anything the user interacted with in the window. Full detail: `reco/results.md` (re-run 2026-10-27 after the post-purchase fix: 3,067 s, 2,981 ranker training lists).

| Stage | Recall@10 | NDCG@10 | NDCG vs popularity | Coverage | Diversity | Pref-cat % | Sub-leak % |
|---|---|---|---|---|---|---|---|
| Popularity (baseline) | 0.172 | 0.156 | 1.00× | 1% | 0.91 | 13% | 4.7% |
| Item-item CF | 0.300 | 0.299 | 1.92× | 13% | 0.25 | 89% | 17.4% |
| ALS | 0.297 | 0.280 | 1.80× | 17% | 0.46 | 69% | 15.6% |
| Content (TF-IDF) | 0.202 | 0.207 | 1.33× | 65% | 0.20 | 91% | 25.8% |
| Merged candidates | 0.347 | 0.361 | 2.32× | 14% | 0.30 | 85% | 20.2% |
| **LightGBM ranker** | **0.394** | **0.411** | **2.64×** | 28% | 0.34 | 84% | 22.5% |
| Full pipeline (ranker + rules) | 0.248 | 0.269 | 1.73× | **35%** | **0.58** | 52% | **2.9%** |

How to read it:

- **Every stage beats popularity; the ranker is best on raw accuracy (2.6× NDCG).**
- **Ground-truth recovery:** item-CF and content put 89–91% of their recommendations in the user's *hidden*
  favourite categories (popularity: 13%). `get_complements` finds 46% of the true complement pairs in its top 10
  and returns **0.0% substitutes**; 34% of `get_similar` results are true substitutes.
- **The business rules cost accuracy on purpose.** "Sub-leak" = recommending substitutes of something the user
  already bought (a 2nd phone the week after buying one). The rules cut it from 22.5% to **2.9%**, while coverage
  rises 28% → 35% and diversity 0.34 → 0.58. Ablation (one rule off at a time): hiding already-bought items is
  the biggest "cost" (+0.041 NDCG if switched off) because shoppers keep re-viewing what they bought, which counts as
  a "hit" offline but is useless as a recommendation; long-tail slots cost almost nothing (+0.002) and keep coverage at 35% instead of 24%.
- **Cold-start users** (439 with no earlier history): NDCG 0.143, the same as **in-stock** popularity; plain
  popularity scores 0.154 (0.93×) only because new users also click sold-out bestsellers the store can't sell.
  Was 0.135 before "shoppers like you" switched from age/gender to city tier.
- Ranker's most-used features: price gap to the user's usual spend, user×item weight, item-CF score, user activity, category affinity.

## 4. Post-purchase intelligence & cold start (examples)

Full tables: `pipeline/report_examples.md` (run on a temp copy of the DB; ground truth only labels the rows).

**Post-purchase switch.** U05835 had a ₹46,399 Redmi flagship phone in the cart; we add the purchase and ask again.

| | Top 8 from the reco engine alone | Top 8 in the storefront (`GET /recommend` = reco + API post-purchase layer) |
|---|---|---|
| Other phones | 5 keypad feature phones, before **and** after the purchase | **0** |
| True complements of the phone | 0 | **3** in slots 1–3: Spigen back cover, JBL 20W charger, tempered glass ("Goes with the Redmi … you just bought") |
| Ground-truth substitutes | 0 | 0 |

`get_complements(phone)` itself is good (4 of its top 5 are true complements, 0 substitutes). The reco engine's
own re-ranking hides true substitutes (similar-price phones) but its complement boost is too weak to lift covers
into the top 8, and ₹1,000 keypad phones don't count as substitutes of a ₹46,000 phone (>4× price rule). The API's
post-purchase layer fixes this for every user-facing list (storefront, `/recommend`), so the shopper sees the
right thing.

**Fixed inside reco (2026-10-27).** Root cause: the substitute rule (same leaf category + within 4× price) also hid
covers and chargers from anyone who owned earbuds. Substitutes now also need the same kind of product; for 14 days
after a purchase the whole leaf category is hidden at any price (except that purchase's complements); first-week
complements get a bigger boost. U05835 after the purchase, reco engine alone: **0 phones, 3 accessories** (2 of them
true complements). Full-pipeline NDCG rose 0.253 → 0.269 (§3). (The table above shows the run before the fix.)

**Cold-start user** (U08992, signed up, no events): six "Popular with shoppers like you" items from six different
categories (flip-flops, socks, screen guard, kurta, shampoo, atta) — popular-with-similar-demographics, kept diverse.

**Cold-start product** ("Red Tape Casual Sneakers (Men, Olive)", launched 2026-09-30, zero interactions):
collaborative filtering knows nothing about it, but `get_similar` still returns five sensible alternatives via the
content model (same brand in women's, then Sparx / Campus / Woodland / Nike sneakers in olive).

## 5. Offer strategies

`python -m pipeline.evaluate_offers` (raw: `pipeline/offer_eval.md`, `pipeline/offer_eval_winback.md`), run on a temp
copy of the DB. Triggers detected at **2026-09-30 19:00** (end of data, so no model sees the future): cart_abandon 552,
post_purchase 588, repurchase_due 532, win_back 1,029 (no festival window then; Diwali starts Oct 20).
For a random 1,500 of them we built the real candidate list (Offer Engine's personal offers + live starter offers),
ranked it with the rec engine and filtered it with the real guardrails. 783 triggers had at least one safe offer.
Four strategies then pick from the **same safe list**:

- **engine** = what the pipeline sends (rec-engine rank #1) · **random** = any safe offer ·
  **max_discount** = the safe offer giving away the most ₹ · **generic** = best store-wide/category starter offer (no personal offers)

Response = the model planted in the data (evaluation only): open 60% × click 25% × redeem
`min(0.95, 0.10 × (1 + 3 × discount × price_sensitivity) × 1.5 if in a favourite category)`; 20% of redemptions would
have happened anyway (holdout), so incremental = 0.8 × redemptions. An offer only counts if it covers the product the
trigger is about. Per 1,000 triggers that had a safe offer:

**Cart abandon + post-purchase + repurchase due (478 triggers)**

| Strategy | Redemptions | Incremental | Discount cost (₹) | ₹ per incremental redemption |
|---|---|---|---|---|
| **engine** | **14.1** | **11.3** | 1,240 | **110** |
| random | 6.2 | 4.9 | 614 | 124 |
| max_discount | 5.4 | 4.3 | 952 | 222 |
| generic | 6.4 | 5.1 | 565 | 111 |

| Trigger | engine | random | max_discount | generic |
|---|---|---|---|---|
| cart_abandon (151) | **17.0** · ₹173 | 3.2 · ₹381 | 1.3 · ₹1,032 | 1.1 · ₹896 |
| post_purchase (164) | **20.5** · ₹85 | 14.4 · ₹76 | 14.0 · ₹155 | 15.3 · ₹72 |
| repurchase_due (163) | **5.1** · ₹17 | 0.6 · ₹14 | 0.4 · ₹92 | 2.4 · ₹36 |
| win_back (208, separate run) | 21.2 · ₹777 | 21.2 · ₹777 | 21.2 · ₹777 | 1.0 · ₹12,371 |

_(cells: redemptions per 1,000 · ₹ discount per incremental redemption)_

What it shows:

- **Engine-ranked beats random by 2.3× on redemptions** (14.1 vs 6.2) and is cheaper per incremental redemption
  (₹110 vs ₹124). The biggest gain is cart abandon (5×): the engine picks the personal "10% off the item you left"
  offer, a random or generic one usually doesn't cover that item.
- **Bigger discounts are not better.** "Give the most ₹" redeems *less* (5.4) and costs twice as much per incremental
  redemption (₹222): large offers tend to be whole-cart or other-item offers that don't match what the user wanted.
- **The engine spends more in total** (₹1,240 vs ₹614 per 1,000 triggers) because more of its offers get used;
  per *incremental* sale it is the cheapest or close to it.
- **Win-back:** after guardrails only one offer per user survives, so the strategies coincide. Personal category
  offers redeem 21× more than generic ones, but are the most expensive trigger (₹777 per incremental redemption):
  a candidate for a smaller win-back discount.
- The observed historical funnel (13,224 simulated sends, Jul–Sep): 12,516 delivered (462 blocked, 246 failed),
  7,463 opened or further, 1,876 clicked or redeemed, **301 redeemed** (2.4% of delivered).

Caveat: the response model is the one that generated the data, so this measures whether the engine exploits the
planted patterns (taste, price sensitivity, item match) — not real-world uplift (§8).

## 6. Guardrails

**LLM guardrails — red team** (`python -m llm.redteam`, **real Claude**, full synthetic catalog, 2026-10-26):
**69/69 attacks blocked (100%)**, **0/14 false positives** on normal shopping questions.

| Category | Blocked | | Category | Blocked |
|---|---|---|---|---|
| prompt injection | 15/15 | | other users' data | 7/7 |
| fake discount demands | 12/12 | | off-topic | 7/7 |
| prompt leak | 7/7 | | jailbreak | 6/6 |
| PII | 5/5 | | sensitive inferences | 5/5 |
| toxicity | 3/3 | | harmful | 2/2 |

On the full catalog the first run scored 68/69: "Who won the cricket match yesterday?" got through because
"cricket" is now a product word (cricket bats), which the off-topic rule treated as shopping intent. Fixed in Phase 7
(`llm/guardrails.py` NEWS_PATTERNS: news/results questions need an explicit shopping word or price); "cricket bat
under 2000", "ipl jersey price" and "any sale on cricket kits today" are still allowed. Re-run with a real Claude key: same result, 69/69 and 0/14.

**Offer guardrails** (from the §5 evaluation, 1,500 triggers): 12,571 ranked candidate offers in, **3,194 kept (25%)**.

| Removed because | Offers |
|---|---|
| would stack with an offer the user holds / a better offer in the list | 8,291 |
| already sent to this user, not used yet | 647 |
| discount above `MAX_DISCOUNT_PCT` or below the margin floor (e.g. `MEGA50`) | 298 |
| high-value offer waiting for admin approval (e.g. `FESTIVE1500`) | 116 |
| category with 18+ products offered to a minor | 16 |
| user already used it the maximum number of times | 9 |

No unsafe offer got through in any test or evaluation run (integration tests check 50% offers, minors, opted-out
users, quiet hours, API-down and LLM-down explicitly).

**Finding — stacking is too strict for the live system.** In one dry-run scheduler round (400 opted-in users linked to a
fake Telegram), of 107 runs only **13 would send**; **86 ended `guardrail_blocked`**, almost all because the user still
"holds" a store-wide starter offer sent months ago. Starter offers are valid until 2027-03-31 and a whole-cart offer
overlaps everything, so one unused July coupon blocks every new offer for months. Safe, but it starves the channel.
Suggested rule change (Phase 3, `phases/NOTES.md`): an unused offer stops counting as "held" after ~14 days, or
whole-cart offers only clash with other whole-cart offers.

**Pipeline safety**: every run writes one audit row per step; a crash anywhere in a module ends as status `error`
with the exception in `audit_log`, never as a send (integration test `test_crash_in_a_module_is_contained`).
Remaining eligibility blocks in the round: daily cap (6), weekly cap (2).

## 7. Integration & end-to-end tests

**Whole suite: 419 tests pass** (`pytest`, 3 min 44 s), including **21 end-to-end integration tests**
(`tests/integration`). Every test now runs on a fresh copy of the sample data with a sample-trained engine, so
results no longer depend on what is loaded in `data/app.db`, and tests can't overwrite `models/reco_bundle.pkl`.

Integration tests use every real module; only Telegram (a fake bot behind the real send queue) and the HTTP hop to
the Offers API (straight into the FastAPI app, with the API key) are faked:

| Scenario | Result |
|---|---|
| New user links Telegram → abandons cart → pipeline sends a personal offer for that item → taps **Apply offer** | `redeemed`, `add_to_cart` event with `source=telegram`, all 7 steps audited in the approved order |
| Second offer the same day | `blocked` (daily cap) |
| Opted-out user (`/stop`) | nothing sent, no `offer_sends` row, **no coupon even created** |
| Quiet hours | `blocked`, nothing sent |
| Offers API down | `offers_api_down`, nothing sent, `fetch_live_offers failed` in `audit_log`, API key never in the log |
| LLM down (every Claude call errors) | `sent` with the template message; `write_offer_message` audited as `fallback` |
| Minor with an 18+ item in the cart | no coupon for that item; the 50% `MEGA50` offer removed by guardrails |
| Dry run | message produced, nothing sent |
| A module crashes mid-pipeline | `error`, audited, nothing sent |
| Each of the 6 trigger types | detected with the right timing rules (e.g. a cart only after 2 idle hours) |
| Scheduler round, then again | sends once, second round skips it (24 h cooldown); dry-run round sends nothing |
| No stubs in production | no module in reco/offers/llm/bot/api/pipeline imports anything from `common.stubs`; `/health` reports all four modules `real` |

**Live check (real processes, real HTTP, full synthetic data):** Offers API :8001 and Main API :8000 started with
uvicorn, `/health` = all modules real, `/offers/live` without key = 401. For one temporarily linked user:
`POST /events` (add to cart) → `POST /admin/run_offer` ran eligibility → Offer Engine (7% personal coupon) →
Offers API (10 live offers) → ranking ("For the Caprese Handbag in your cart") → guardrails (50% offer and 3
overlapping ones removed) → message → revalidate → bot, which stopped at "TELEGRAM_BOT_TOKEN is not set".
`python -m pipeline.scheduler --once --dry-run` found 9,396 triggers (Diwali window), skipped 9,395 unreachable users,
and showed the `DIWALI200` message it would send. Test changes were removed from the demo DB afterwards.

**Performance fix found during integration:** `offers.generator` loaded every user's events (and joined all 1M
events) on every call — tens of seconds per pipeline run on full data. It now loads only the one user's events:
~0.3–0.6 s per call.

## 8. Limitations & future work

**Verified since the first version of this report (2026-10-27)**

- **Real Telegram:** @Sutlejbot runs with a real token and answers on a real phone. All 7 demo shoppers
  (`bot/personas.py`) were run through the full pipeline (dry run, full data, real Claude): Claude chose the offer
  (or "no discount" for the premium regular) and the rules checked it.
- **Real Claude:** the red team was re-run with a key (69/69 blocked, 0/14 false positives, §6). An unreadable
  chooser reply fell back to the rec engine's pick as designed.
- **Post-purchase inside reco** and **cold start** were fixed (§3, §4).

**Not verified yet**

- **Live uplift.** The 10% control group (`offers/rules.yaml`) is in place, but there are not yet enough real
  sends to compare the groups.
- **A real external dataset.** The external path was smoke-tested on the RetailRocket-format fixture, not on a downloaded dataset.

**Limitations**

- **Offer stacking starves the channel** (§6): one unused long-lived store-wide coupon blocks new offers for months.
  Demo shoppers are picked among users without such coupons.
- **Win-back offers are expensive** (₹777 per incremental redemption, §5); the win-back starting discount can be
  lowered in the Offer Control Room.
- **Reward points** are not modelled yet (the `points` offer type exists in the contract, but there is no points balance).

- Synthetic data: the response model used to compare offer strategies is the same one that generated the data,
  so §5 measures "does the engine exploit the patterns we planted", not real-world uplift. A live A/B test with a holdout is the real proof.
- Offline offer evaluation is a single snapshot (2026-09-30): `reco.rank_offers_for_user` uses "now = the user's
  latest event", so evaluating at past dates would leak future events (note to Phase 2 in `phases/NOTES.md`).
- `price_drop` only works going forward: the contract has no price history, so the scheduler starts recording prices
  (`price_history`) on its first run.
- Models are retrained nightly (03:00) by the scheduler, but the API and bot keep the model they loaded until restarted.
- Gift mode / budget are stored twice (API: `data/user_prefs.json`, bot: `bot_user_prefs`) and can disagree.
  Extra tables outside the Data Contract: `price_history`, `pipeline_runs`, `offer_price_snapshots`, `bot_user_prefs`.
- SQLite + one machine: fine for a demo, not for many concurrent writers.

**Future work**

- Shared `user_prefs` / `user_feedback` tables; use "Not interested because…" as a training signal.
- Back-testable reco (`now=` parameter) and a learned uplift model to replace today's "likely buyer" rule (which
  already sends premium regulars a reminder instead of a discount).
- Telegram webhook deployment, Postgres, a proper job queue; hot-reload models after the nightly retrain.
- Multilingual offer wording (Hindi/Hinglish) and per-user best send time.
