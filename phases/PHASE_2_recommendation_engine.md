# PHASE 2 — Recommendation Engine (parallel)

> Paste everything below into a new chat with the project folder selected. Phase 0 must be done.

---

You are building **Phase 2 (Recommendation Engine)** of the E-commerce Recommendation Engine.

**Before anything:** read `CLAUDE.md` (Module Contracts → `reco`, Data Contract, rules), `phases/NOTES.md`. Work with `data/sample/` now; it must also work on `data/raw/` once Phase 1 finishes (switch via `DATA_SOURCE` in `.env`). I am a beginner — explain each algorithm in simple words before coding it.

## Goal
Multi-stage recommender: candidate generation → ranker → re-ranking, with an evaluation harness and an explanation (`reason`) for every recommendation.

## You own
`reco/`, `models/`, `tests/reco/`. Do not edit `common/`.

## Build (in this order — evaluate after each step)
1. **Data prep** (`reco/data.py`): load events, apply event weights from `CLAUDE.md`, build user×item sparse matrix, time-based split (train/val/test dates in `CLAUDE.md`).
2. **Evaluation harness FIRST** (`reco/evaluate.py`): Recall@10, NDCG@10, MAP@10, catalog coverage, diversity (intra-list category variety), novelty; plus **ground-truth check**: % of recs in user's hidden preferred categories, and % of complement pairs found by item-CF (uses `data/ground_truth/`, evaluation only). Outputs a markdown table to `reco/results.md`.
3. **Baselines** (`reco/candidates/popular.py`): global + per-category trending (recent window).
4. **Item-item CF** (`reco/candidates/item_cf.py`): co-occurrence / cosine → `get_similar`, and purchase-based co-buy → `get_complements`.
5. **ALS** (`reco/candidates/als.py`): `implicit` library on weighted matrix (fallback to scikit-learn TruncatedSVD if `implicit` won't install — note it in README).
6. **Content-based** (`reco/candidates/content.py`): TF-IDF on title+description+tags (cold start for new items/users).
7. **Candidate merge** (`reco/candidates/merge.py`): ~200 per user from all sources, remembering which source produced each (for `reason`).
8. **Ranker** (`reco/ranker.py`): LightGBM LambdaRank with features: candidate-source scores, user category affinity, item popularity/recency, price vs. user's usual price band, rating, days since last view. Compare before/after in `results.md`.
9. **Re-ranking rules** (`reco/rerank.py`): remove out-of-stock, remove already-bought non-consumables **and their substitutes** (post-purchase intelligence), boost complements of recent purchases, respect budget band, max 3 per brand/category per list, guarantee ~20% long-tail slots, hide age-restricted items for minors, gift mode → don't use/update personal history.
10. **Reasons** (`reco/explain.py`): human-readable reason per item, e.g. "Because you bought Redmi Note 13", "Trending in Sports this week", "Within your usual budget".
11. **Public API** (`reco/__init__.py`): every `reco` function in Module Contracts, incl. `rank_offers_for_user` (score each offer by relevance to user's top categories, cart/wishlist items, budget band, price sensitivity) and `build_user_context`.
12. **Train script**: `python -m reco.train` trains everything and saves to `models/`; `python -m reco.evaluate` writes `reco/results.md`.

**Data-level awareness:** must also run on real external data (`DATA_SOURCE=external`, see `docs/DATA_STANDARD.md`). Use `common.db.feature_enabled()` — e.g. no catalog → skip content model; no ground truth → skip ground-truth metrics; no users table → skip demographic features. Never crash on missing optional columns.

## Done when
- [ ] All contract functions work and return `RecItem`/`RankedOffer` with non-empty `reason`
- [ ] `reco/results.md` shows each stage vs. popularity baseline (on sample data now; re-run on full data later)
- [ ] Post-purchase test: after buying a phone, no other phones in top-10, complements appear
- [ ] Cold-start user and new product both get sensible recs
- [ ] `tests/reco` passes; `reco/README.md` written; Phase 2 ticked in `phases/PROGRESS.md`
