# Recommendation engine — evaluation results (sample data, 50 users)

_Generated 2026-10-23T09:22:39 by `python -m reco.evaluate` in 33s._

- **Data:** `DATA_SOURCE=sample` (level L4) — 2,160 events, 49 users, 100 products
- **Split:** train ≤ 2026-08-31 · validation 2026-09-01…2026-09-15 · test 2026-09-16…2026-09-30
- **Relevant items** = anything the user viewed/clicked/wishlisted/carted/bought in the window. Recall is divided by min(#relevant, 10).
- ALS backend: `implicit` · ranker: trained on 32 user-windows

## Test window — headline (21 users)

Models trained on everything up to the end of validation; scored on the untouched test window.

| Stage | Recall@10 | NDCG@10 | MAP@10 | NDCG vs baseline | Coverage | Diversity | Novelty | Pref-cat % | Sub-leak % |
|---|---|---|---|---|---|---|---|---|---|
| Popularity (baseline) | 0.241 | 0.197 | 0.091 | 1.00× | 21% | 0.65 | 5.4 | 28% | 14.3% |
| Item-item CF | 0.449 | 0.378 | 0.235 | 1.91× | 55% | 0.46 | 6.0 | 73% | 28.1% |
| ALS | 0.453 | 0.369 | 0.222 | 1.87× | 70% | 0.54 | 6.4 | 68% | 24.8% |
| Content (TF-IDF) | 0.569 | 0.454 | 0.295 | 2.30× | 73% | 0.28 | 6.7 | 94% | 35.7% |
| Merged candidates (blend) | 0.522 | 0.444 | 0.293 | 2.25× | 65% | 0.44 | 6.1 | 80% | 32.4% |
| LightGBM ranker | 0.475 | 0.456 | 0.312 | 2.31× | 76% | 0.35 | 6.6 | 87% | 32.4% |
| Full pipeline (ranker + rules) | 0.299 | 0.268 | 0.155 | 1.36× | 72% | 0.50 | 6.6 | 53% | 8.6% |

## Validation window (15 users)

Candidate generators trained ≤ train end; used for tuning.

| Stage | Recall@10 | NDCG@10 | MAP@10 | NDCG vs baseline | Coverage | Diversity | Novelty | Pref-cat % | Sub-leak % |
|---|---|---|---|---|---|---|---|---|---|
| Popularity (baseline) | 0.396 | 0.301 | 0.169 | 1.00× | 19% | 0.84 | 5.0 | 27% | 12.7% |
| Item-item CF | 0.501 | 0.363 | 0.213 | 1.21× | 53% | 0.47 | 5.9 | 66% | 20.7% |
| ALS | 0.474 | 0.375 | 0.233 | 1.25× | 60% | 0.58 | 6.3 | 66% | 20.0% |
| Content (TF-IDF) | 0.513 | 0.415 | 0.274 | 1.38× | 64% | 0.33 | 6.6 | 91% | 25.3% |
| Merged candidates (blend) | 0.551 | 0.504 | 0.354 | 1.68× | 57% | 0.48 | 6.0 | 76% | 22.7% |

## Re-ranking rules — what each one costs or gains (test window)

Each row = the full pipeline with ONE rule switched off. Δ NDCG > 0 means the rule costs accuracy (often on purpose: e.g. users re-view products they already bought, but recommending those is useless).

| Rule switched OFF | NDCG@10 | Δ NDCG | Coverage | Diversity | Sub-leak % |
|---|---|---|---|---|---|
| _(none — all rules on)_ | 0.268 | — | 72% | 0.50 | 8.6% |
| Remove out-of-stock | 0.271 | +0.002 | 72% | 0.49 | 8.6% |
| Hide age-restricted for minors | 0.268 | +0.000 | 72% | 0.50 | 8.6% |
| Hide already-bought items (+ their substitutes) | 0.417 | +0.149 | 69% | 0.46 | 24.8% |
| Hide substitutes of bought items | 0.322 | +0.053 | 73% | 0.48 | 16.2% |
| Add complements + restock reminders | 0.283 | +0.015 | 71% | 0.54 | 9.0% |
| Push down over-budget items | 0.286 | +0.018 | 72% | 0.50 | 8.6% |
| Max 3 per brand/category | 0.273 | +0.005 | 75% | 0.42 | 9.0% |
| 20% long-tail slots | 0.268 | +0.000 | 72% | 0.50 | 8.6% |

## Product relations vs. ground truth

| Check | Value | Meaning |
|---|---|---|
| Complement recall@10 | 59% | true complement pairs found by `get_complements` |
| Complement precision@10 | 18% | returned complements that are true complements |
| Substitutes in complements | 0.0% | should be ~0 (a phone is not a phone's complement) |
| `get_similar` are substitutes | 22% | similar items that are true substitutes |

## Ranker — most-used features

| Feature | Splits |
|---|---|
| price_gap | 275 |
| log_price_ratio | 222 |
| score_item_cf | 206 |
| cat_affinity | 171 |
| item_age_days | 166 |
| score_popularity | 146 |
| score_als | 125 |
| score_content | 122 |

## How to read this

- **NDCG vs baseline** > 1.00× means the stage beats plain popularity.
- The full pipeline trades a little raw accuracy for business rules: no out-of-stock items, no substitutes of things already bought (see Sub-leak), budget fit, max 3 per brand/category, ~20% long-tail slots (see Coverage/Novelty).
- On the tiny sample data (50 users) numbers are noisy — re-run on `DATA_SOURCE=synthetic` after Phase 1 for meaningful comparisons.
