# Recommendation engine — evaluation results

_Generated 2026-10-23T14:14:05 by `python -m reco.evaluate` in 2223s._

- **Data:** `DATA_SOURCE=synthetic` (level L4) — 1,038,441 events, 8,975 users, 2,916 products
- **Split:** train ≤ 2026-08-31 · validation 2026-09-01…2026-09-15 · test 2026-09-16…2026-09-30
- **Relevant items** = anything the user viewed/clicked/wishlisted/carted/bought in the window. Recall is divided by min(#relevant, 10).
- ALS backend: `implicit` · ranker: trained on 2982 user-windows

## Test window — headline (1000 users)

Models trained on everything up to the end of validation; scored on the untouched test window.

| Stage | Recall@10 | NDCG@10 | MAP@10 | NDCG vs baseline | Coverage | Diversity | Novelty | Pref-cat % | Sub-leak % |
|---|---|---|---|---|---|---|---|---|---|
| Popularity (baseline) | 0.167 | 0.152 | 0.071 | 1.00× | 1% | 0.97 | 6.1 | 13% | 4.4% |
| Item-item CF | 0.300 | 0.299 | 0.174 | 1.97× | 13% | 0.25 | 8.7 | 89% | 17.4% |
| ALS | 0.296 | 0.280 | 0.158 | 1.84× | 17% | 0.47 | 8.0 | 69% | 15.6% |
| Content (TF-IDF) | 0.202 | 0.207 | 0.113 | 1.36× | 65% | 0.21 | 11.1 | 91% | 25.8% |
| Merged candidates (blend) | 0.346 | 0.362 | 0.226 | 2.39× | 14% | 0.31 | 8.4 | 85% | 20.2% |
| LightGBM ranker | 0.393 | 0.409 | 0.268 | 2.70× | 28% | 0.35 | 8.4 | 83% | 22.4% |
| Full pipeline (ranker + rules) | 0.234 | 0.253 | 0.155 | 1.67× | 36% | 0.59 | 8.3 | 50% | 2.5% |

## Validation window (1000 users)

Candidate generators trained ≤ train end; used for tuning.

| Stage | Recall@10 | NDCG@10 | MAP@10 | NDCG vs baseline | Coverage | Diversity | Novelty | Pref-cat % | Sub-leak % |
|---|---|---|---|---|---|---|---|---|---|
| Popularity (baseline) | 0.169 | 0.159 | 0.076 | 1.00× | 1% | 0.94 | 5.9 | 13% | 4.3% |
| Item-item CF | 0.291 | 0.289 | 0.168 | 1.82× | 14% | 0.26 | 8.7 | 87% | 16.1% |
| ALS | 0.297 | 0.277 | 0.157 | 1.75× | 17% | 0.48 | 7.9 | 68% | 14.9% |
| Content (TF-IDF) | 0.208 | 0.209 | 0.115 | 1.32× | 68% | 0.22 | 11.1 | 90% | 23.5% |
| Merged candidates (blend) | 0.342 | 0.357 | 0.223 | 2.25× | 15% | 0.33 | 8.3 | 83% | 19.1% |

## Re-ranking rules — what each one costs or gains (test window)

Each row = the full pipeline with ONE rule switched off. Δ NDCG > 0 means the rule costs accuracy (often on purpose: e.g. users re-view products they already bought, but recommending those is useless).

| Rule switched OFF | NDCG@10 | Δ NDCG | Coverage | Diversity | Sub-leak % |
|---|---|---|---|---|---|
| _(none — all rules on)_ | 0.253 | — | 36% | 0.59 | 2.5% |
| Remove out-of-stock | 0.278 | +0.025 | 36% | 0.60 | 2.4% |
| Hide age-restricted for minors | 0.253 | +0.000 | 36% | 0.59 | 2.5% |
| Hide already-bought items (+ their substitutes) | 0.321 | +0.068 | 37% | 0.53 | 18.8% |
| Hide substitutes of bought items | 0.293 | +0.040 | 36% | 0.54 | 7.8% |
| Add complements + restock reminders | 0.273 | +0.020 | 32% | 0.61 | 2.5% |
| Push down over-budget items | 0.257 | +0.004 | 36% | 0.58 | 2.4% |
| Max 3 per brand/category | 0.274 | +0.021 | 38% | 0.40 | 3.2% |
| 20% long-tail slots | 0.252 | -0.001 | 22% | 0.65 | 2.0% |

## Cold-start users in the test window (439 users with no earlier history)

| Stage | Recall@10 | NDCG@10 | MAP@10 | NDCG vs baseline | Coverage | Diversity | Novelty | Pref-cat % | Sub-leak % |
|---|---|---|---|---|---|---|---|---|---|
| Popularity (baseline) | 0.175 | 0.152 | 0.073 | 1.00× | 0% | 0.99 | 6.0 | 12% | 0.0% |
| Full pipeline (ranker + rules) | 0.148 | 0.135 | 0.065 | 0.89× | 1% | 0.99 | 7.3 | 12% | 0.0% |

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
| price_gap | 207 |
| user_item_weight | 186 |
| score_item_cf | 181 |
| user_activity | 174 |
| log_price_ratio | 169 |
| cat_affinity | 157 |
| days_since_seen | 136 |
| item_trend | 131 |

## How to read this

- **NDCG vs baseline** > 1.00× means the stage beats plain popularity.
- The full pipeline trades a little raw accuracy for business rules: no out-of-stock items, no substitutes of things already bought (see Sub-leak), budget fit, max 3 per brand/category, ~20% long-tail slots (see Coverage/Novelty).
- Scored on a fixed random sample of 1,000 users per window (NDCG standard error ≈ 0.01).
- Ground truth (hidden preferences, product relations) is used ONLY here, never as model input.
