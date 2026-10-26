# Recommendation engine — evaluation results

_Generated 2026-10-27T02:01:52 by `python -m reco.evaluate` in 3067s._

- **Data:** `DATA_SOURCE=synthetic` (level L4) — 1,038,441 events, 8,975 users, 2,916 products
- **Split:** train ≤ 2026-08-31 · validation 2026-09-01…2026-09-15 · test 2026-09-16…2026-09-30
- **Relevant items** = anything the user viewed/clicked/wishlisted/carted/bought in the window. Recall is divided by min(#relevant, 10).
- ALS backend: `implicit` · ranker: trained on 2981 user-windows

## Test window — headline (1000 users)

Models trained on everything up to the end of validation; scored on the untouched test window.

| Stage | Recall@10 | NDCG@10 | MAP@10 | NDCG vs baseline | Coverage | Diversity | Novelty | Pref-cat % | Sub-leak % |
|---|---|---|---|---|---|---|---|---|---|
| Popularity (baseline) | 0.172 | 0.156 | 0.073 | 1.00× | 1% | 0.91 | 6.0 | 13% | 4.7% |
| Item-item CF | 0.300 | 0.299 | 0.174 | 1.92× | 13% | 0.25 | 8.7 | 89% | 17.4% |
| ALS | 0.297 | 0.280 | 0.159 | 1.80× | 17% | 0.46 | 8.0 | 69% | 15.6% |
| Content (TF-IDF) | 0.202 | 0.207 | 0.113 | 1.33× | 65% | 0.20 | 11.1 | 91% | 25.8% |
| Merged candidates (blend) | 0.347 | 0.361 | 0.225 | 2.32× | 14% | 0.30 | 8.4 | 85% | 20.2% |
| LightGBM ranker | 0.394 | 0.411 | 0.270 | 2.64× | 28% | 0.34 | 8.4 | 84% | 22.5% |
| Full pipeline (ranker + rules) | 0.248 | 0.269 | 0.167 | 1.73× | 35% | 0.58 | 8.2 | 52% | 2.9% |

## Validation window (1000 users)

Candidate generators trained ≤ train end; used for tuning.

| Stage | Recall@10 | NDCG@10 | MAP@10 | NDCG vs baseline | Coverage | Diversity | Novelty | Pref-cat % | Sub-leak % |
|---|---|---|---|---|---|---|---|---|---|
| Popularity (baseline) | 0.169 | 0.160 | 0.078 | 1.00× | 1% | 0.89 | 6.0 | 13% | 4.6% |
| Item-item CF | 0.291 | 0.289 | 0.168 | 1.80× | 14% | 0.25 | 8.7 | 87% | 16.1% |
| ALS | 0.297 | 0.277 | 0.157 | 1.73× | 17% | 0.47 | 7.9 | 68% | 14.9% |
| Content (TF-IDF) | 0.208 | 0.209 | 0.114 | 1.31× | 68% | 0.21 | 11.1 | 90% | 23.5% |
| Merged candidates (blend) | 0.340 | 0.355 | 0.222 | 2.22× | 15% | 0.32 | 8.3 | 83% | 19.1% |

## Re-ranking rules — what each one costs or gains (test window)

Each row = the full pipeline with ONE rule switched off. Δ NDCG > 0 means the rule costs accuracy (often on purpose: e.g. users re-view products they already bought, but recommending those is useless).

| Rule switched OFF | NDCG@10 | Δ NDCG | Coverage | Diversity | Sub-leak % |
|---|---|---|---|---|---|
| _(none — all rules on)_ | 0.269 | — | 35% | 0.58 | 2.9% |
| Remove out-of-stock | 0.299 | +0.030 | 36% | 0.59 | 2.8% |
| Hide age-restricted for minors | 0.269 | +0.000 | 35% | 0.58 | 2.9% |
| Hide already-bought items (+ their substitutes) | 0.311 | +0.041 | 36% | 0.55 | 17.4% |
| Hide substitutes of bought items | 0.283 | +0.013 | 35% | 0.55 | 7.2% |
| Add complements + restock reminders | 0.291 | +0.021 | 31% | 0.60 | 2.8% |
| Push down over-budget items | 0.273 | +0.004 | 35% | 0.57 | 2.8% |
| Max 3 per brand/category | 0.296 | +0.027 | 38% | 0.35 | 4.0% |
| 20% long-tail slots | 0.271 | +0.002 | 24% | 0.61 | 2.7% |

## Cold-start users in the test window (439 users with no earlier history)

New users click sold-out bestsellers too, and plain popularity gets credit for showing them — but the store can't sell those. **In-stock popularity** is the fair yardstick for the full pipeline.

| Stage | Recall@10 | NDCG@10 | MAP@10 | NDCG vs baseline | Coverage | Diversity | Novelty | Pref-cat % | Sub-leak % |
|---|---|---|---|---|---|---|---|---|---|
| Popularity (baseline) | 0.180 | 0.154 | 0.074 | 1.00× | 0% | 0.93 | 5.9 | 13% | 0.0% |
| Popularity, in stock only | 0.161 | 0.143 | 0.069 | 0.93× | 0% | 0.83 | 6.1 | 13% | 0.0% |
| Full pipeline (ranker + rules) | 0.161 | 0.143 | 0.069 | 0.93× | 0% | 0.83 | 6.1 | 13% | 0.0% |

## Product relations vs. ground truth

| Check | Value | Meaning |
|---|---|---|
| Complement recall@10 | 46% | true complement pairs found by `get_complements` |
| Complement precision@10 | 15% | returned complements that are true complements |
| Substitutes in complements | 0.0% | should be ~0 (a phone is not a phone's complement) |
| `get_similar` are substitutes | 34% | similar items that are true substitutes |

## Ranker — most-used features

| Feature | Splits |
|---|---|
| price_gap | 227 |
| item_trend | 190 |
| score_item_cf | 179 |
| user_item_weight | 176 |
| log_price_ratio | 174 |
| user_activity | 162 |
| cat_affinity | 146 |
| days_since_seen | 130 |

## How to read this

- **NDCG vs baseline** > 1.00× means the stage beats plain popularity.
- The full pipeline trades a little raw accuracy for business rules: no out-of-stock items, no substitutes of things already bought (see Sub-leak), budget fit, max 3 per brand/category, ~20% long-tail slots (see Coverage/Novelty).
- Scored on a fixed random sample of 1,000 users per window (NDCG standard error ≈ 0.01).
- Ground truth (hidden preferences, product relations) is used ONLY here, never as model input.
