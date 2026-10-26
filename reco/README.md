# reco/ — Recommendation Engine (Phase 2)

Recommends products to users and **explains every recommendation** ("Because you viewed Redmi Note 13",
"Goes well with your Samsung Galaxy M35", "Time to restock Bru Premium Tea?").

```
              ┌── popularity / trending ──┐
user history ─┼── item-item CF ───────────┼─> merge (~200) ─> LightGBM ranker ─> re-ranking rules ─> top-k + reasons
              ├── ALS (matrix factor.) ───┤
              └── content (TF-IDF) ───────┘
```

## Use it (other modules)

```python
from reco import (get_recommendations, get_similar, get_complements, get_trending,
                  rank_offers_for_user, build_user_context)

get_recommendations("U00001", k=10)                       # list[RecItem(product_id, score, reason, source)]
get_recommendations("U00001", context={"is_gift_mode": True, "top_categories": ["C023"]})
get_recommendations("U00001", context={"budget_band": "low"})   # user-chosen budget = hard filter
get_similar("P00001")        # alternatives (works for brand-new products via their text)
get_complements("P00001")    # phone -> cover, charger
get_trending(k=10, category_id="C001")   # a parent category includes its children
rank_offers_for_user("U00001", offers)   # list[RankedOffer(offer_id, score, reason)], best first
build_user_context("U00001")             # UserContext(budget_band, top_categories)
```

`context` may be a `UserContext` or a plain dict. Scores are scaled so the best item = 1.0.

## Run it

```bash
python -m reco.train       # trains everything -> models/reco_bundle.pkl
python -m reco.evaluate    # backtest -> reco/results.md (add --out file.md to write elsewhere)
pytest tests/reco          # 31 tests, ~30 s (uses a temp copy of the sample data, never app.db)
```

| Data | `reco.train` | `reco.evaluate` |
|---|---|---|
| sample (2k events) | ~6 s | ~35 s |
| synthetic full (1M events, 10k users, 3k products), 8 GB laptop | ~10 min | ~37 min |

**Always run `python -m reco.train` after switching `DATA_SOURCE` or loading new data.** If the bundle
is missing or was trained on another `DATA_SOURCE`, the first API call trains on the fly, which on
full data blocks that call for ~10 minutes.

Models are trained in batch, but **each user's own history is read fresh from `app.db` on every call**,
so a purchase logged a second ago immediately changes that user's recommendations.

## The algorithms in simple words

| Step | File | What it does |
|---|---|---|
| 1. Data prep | `data.py` | Loads tables (app.db, CSV fallback), sums event weights per user×product (view 1 … purchase 6), builds the sparse user×item matrix, splits by time. Gift-session events are **excluded** from personal taste. |
| 2. Evaluation | `evaluate.py` | Backtest: train on the past, check top-10 lists against what users did next. Recall/NDCG/MAP@10, coverage, diversity, novelty, ground-truth checks, rule ablation. |
| 3. Popularity | `candidates/popular.py` | All-time popular + **trending** (last 14 days, half-life 7 days) + "popular with people like you" (same city tier — tier tracks price sensitivity). The baseline everything must beat. |
| 4. Item-item CF | `candidates/item_cf.py` | "People who liked X also liked Y" — cosine similarity between products' audiences (with shrinkage so one shared user isn't "perfect"). **Complements** = bought by the same user within 30 days, *different* category, weighted by how typical that category pair is. |
| 5. ALS | `candidates/als.py` | Learns ~32 hidden "taste numbers" per product (`implicit` library). A user's numbers are solved from their latest history on each call ("fold-in"). Falls back to scikit-learn `TruncatedSVD` if `implicit` can't be installed. |
| 6. Content | `candidates/content.py` | TF-IDF on title + brand + category + tags + description. Rare words ("galaxy", "yoga") matter, boilerplate doesn't. Gives brand-new products a chance (cold start). |
| 7. Merge | `candidates/merge.py` | ~200 candidates per user from all sources, each source's score scaled 0..1, plus the "anchor" product for the reason. |
| 8. Ranker | `ranker.py` | LightGBM **LambdaRank** reorders candidates using 18 features (source scores, category affinity, popularity, trend, price vs. the user's usual level, rating, recency, cart/wishlist…). Trained on two rolling 15-day windows **before** the test period (≤1,500 users each). Skipped for users with no history; their features would be empty. |
| 9. Rules | `rerank.py` | Out-of-stock removed · minors never see age-restricted items · bought non-consumables hidden, their **substitutes hidden for 60 days** · **just bought (14 days): the whole leaf category hidden**, except complements of that purchase · **complements of recent purchases boosted** (much harder in the first 7 days) · restock reminders for consumables · soft budget fit (hard filter if the user chose a band) · max 3 per brand/category per 10 items · ~20% long-tail slots · gift mode ignores personal history. |
| 10. Reasons | `explain.py` | One sentence per item from its strongest source + anchor product, plus "· Within your usual budget". Built only from our own data. |
| 11. API | `service.py`, `offer_ranking.py` | Contract functions; offer scoring = product match (cart/wishlist/bundle/buy-again) + category match + value for price-sensitive users − min-cart/segment mismatch. |
| 12. Train | `train.py` | Ranker on rolling windows + all candidate models on ALL data → `models/reco_bundle.pkl`. |

**Substitute vs. complement** (post-purchase intelligence): a substitute is something you'd buy *instead*
(same leaf category, same **kind**, within 4× the price); a complement goes *with* it
(bought together, different category). The kind is the last word of the title before any "(":
"Redmi Flagship 5G Smartphone (12GB/256GB)" → smartphone, "Spigen Back Cover (...)" → cover. So owning
earbuds no longer hides phone covers and chargers, which share the "Mobile Accessories" category.
Categories whose titles don't give a few clear kinds (books, exam guides) count as one kind.
For the first 14 days after a purchase the whole leaf category is hidden at any price and kind
(no ₹1.8k keypad phone after a ₹46k flagship).

**"Now"** for a user = the later of their latest event and the latest event in the data (our data ends
2026-09-30, so the real clock would make every purchase look ancient).

## Works with partial data (levels L1–L4)

Uses `common.db.feature_enabled()`:

| Missing | What switches off |
|---|---|
| products (L1) | content model, ranker (blend is used instead), categories in reasons ("Popular right now") |
| users / demographics | "popular with people like you" (plain trending instead) |
| orders | purchases still come from `purchase` events |
| ground truth (real data) | ground-truth rows of `results.md` |

Dates outside our 2026 window → the split becomes proportional (last ~16% of days = validation, last ~8% = test).
Tested in `tests/reco/test_behaviour.py::test_runs_on_events_only_data`.

## Results

`reco/results.md` = full synthetic data (1M events; 1,000 sampled users per window).
`reco/results_sample.md` = the 50-user sample data (noisy, kept for reference).

Test window, NDCG@10 vs. the popularity baseline:

| Popularity | Item-CF | ALS | Content | Blend | **LightGBM ranker** | Full pipeline (ranker + rules) |
|---|---|---|---|---|---|---|
| 1.00× | 1.97× | 1.84× | 1.36× | 2.39× | **2.70×** | 1.67× |

- **The ranker adds +13% over the blend.** Its most-used clues are price fit, the user's past
  weight on the item, item-CF score and category affinity.
- **The full pipeline is lower on raw accuracy, on purpose.** The ablation table shows the cost
  comes from hiding already-bought items and their substitutes: users in the data keep re-viewing
  things they own, which counts as a "hit", but recommending them is useless. Sub-leak (substitutes
  of owned items in the list) drops from 22% to 2.5%, and diversity goes 0.35 → 0.59.
- Product relations vs. ground truth: `get_complements` finds 46% of true complement pairs with
  **0%** substitutes mixed in. 34% of `get_similar` results are true substitutes; with only a few
  true substitutes per product, that is near the ceiling for a top-10.
- **Weak spot: cold-start users** (no history) get 0.89× of plain popularity. The rules (no
  out-of-stock, long-tail slots, brand/category caps) push them away from the bestsellers
  newcomers mostly click. See "Known limitations".

## Tuning knobs (constants at the top of each file)

`rerank.py`: `SUBSTITUTE_BLOCK_DAYS`, `SAME_CATEGORY_BLOCK_DAYS`, `RECENT_PURCHASE_DAYS`, `FRESH_PURCHASE_DAYS`, `COMPLEMENT_BOOST`, `FRESH_COMPLEMENT_BOOST`, `RESTOCK_BOOST`,
`MAX_PER_BRAND/CATEGORY`, `LONG_TAIL_SHARE`, `BUDGET_LIMITS` · `merge.py`: `BLEND_WEIGHTS`,
`CANDIDATES_PER_USER` · `item_cf.py`: `SHRINKAGE`, `CO_BUY_WINDOW_DAYS`, `MIN_CATEGORY_CONFIDENCE` ·
`popular.py`: `TRENDING_DAYS`, `HALF_LIFE_DAYS`, `SEGMENT_SHARE`, `SEGMENT_COLUMNS` · `engine.py`: `SUBSTITUTE_PRICE_RATIO`, `MAX_KINDS_PER_CATEGORY` · `ranker.py`: `MAX_USERS_PER_WINDOW` (1,500) ·
`evaluate.py`: `MAX_EVAL_USERS` (1,000 sampled users on big data).

## Performance notes (pandas 3)

pandas 3 stores text as Arrow strings. Their `.isin()` is ~100× slower than normal and **aborts the
process** on million-row inputs, so reco code uses `reco.data.isin()` (a plain Python-set check)
everywhere instead. Per-user tables are grouped once in `all_histories()`, never filtered per user.

## Known limitations

- Cold-start users: the full pipeline is below plain trending (0.89×). Options to try: lighter
  rules for users with no history (no long-tail reservation), or a demographic-segment model.
  Tune on the validation window, not the test window.
- On the tiny sample data the ranker has only ~30 training lists, so it is close to the simple blend.
- Complement precision@10 looks low (~18%) because most products have only 1–3 true complements; recall is the number to watch.
- Re-train regularly (e.g. nightly, Phase 7's scheduler): new products get CF/ALS signal only after training. Content similarity covers them until then.
