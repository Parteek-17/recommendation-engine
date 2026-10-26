# PHASE 1 — Synthetic Data Generator (parallel)

> Paste everything below into a new chat with the project folder selected. Phase 0 must be done.

---

You are building **Phase 1 (Data Generator)** of the E-commerce Recommendation Engine.

**Before anything:** read `CLAUDE.md` (especially Data Contract + parallel-safety rules), **`docs/DATA_PARAMETERS.md` (all default values — put them in `data_gen/config.py`)**, `phases/NOTES.md`, and look at `data/sample/` to match the format exactly. I am a beginner — explain each step briefly and keep code simple and commented.

## Goal
Generate a realistic synthetic Indian general-marketplace dataset with **planted patterns**, so models have something real to learn and we can measure them against hidden ground truth.

## You own
`data_gen/`, `ingest/`, `data/raw/`, `data/ground_truth/`, `data/processed/`, `tests/data_gen/`, `tests/ingest/`. Do not edit `common/`.

## Scale & window
~10,000 users · ~3,000 products · ~25 categories (8 parents: Electronics, Fashion, Home & Kitchen, Beauty, Grocery, Sports, Books, Toys) · ~1M events · 2026-04-01 → 2026-09-30. Everything driven by `data_gen/config.py` (sizes, probabilities, seed) so we can make a small version quickly.

## Build (one file per step, run via `python -m data_gen.generate --size small|full`)
1. **Catalog** (`catalog.py`): categories tree; products with realistic titles/descriptions from templates (brand + type + attributes), category-specific price ranges in ₹, `mrp ≥ price`, `margin_pct`, ratings, stock, tags; ~10% launched late (cold-start items); consumables with `repurchase_days`; some `age_restricted`.
2. **Product relations** (`relations.py`): complements (phone→cover/charger, shoes→socks, laptop→bag/mouse) and substitutes (same category + similar price). Save to `data/ground_truth/product_relations.csv`.
3. **Users** (`users.py`): demographics, city tiers; hidden profile per user: 2–3 preferred categories, `price_sensitivity` 0–1, `activity_level` (power-law), `is_gifter`. ~10% sign up in the last month (cold start). Save hidden profile to `data/ground_truth/user_hidden_profile.csv`.
4. **Behaviour simulation** (`simulate.py`), day by day:
   - sessions per user from activity level; ~80% browsing in preferred categories, ~20% exploration
   - long-tail product popularity (power-law)
   - funnel: view → click → add_to_cart (~10%) → purchase (~30% of carts), higher if product matches taste + price comfort
   - after buying a non-consumable → much less interest in its substitutes, more in complements
   - consumables re-bought around `repurchase_days` ± noise
   - sale periods (e.g. Aug 10–15) → traffic + conversion spike; a few "viral" products for 2–3 weeks
   - gift sessions (`is_gift=1`) browse outside the user's usual categories
   - searches with realistic queries; remove_from_cart; random noise clicks
5. **Orders, order_items, reviews** derived from purchases (~5% review rate, rating correlated with product quality).
6. **Offers + consent seed data**: a starter `offers.csv` (~60 offers across all offer_types/trigger_types), `user_consent.csv` (~60% opted in), empty `telegram_links.csv`, `offer_sends.csv` with simulated past responses (opened/clicked/redeemed probabilities depend on discount size + price sensitivity → enables uplift analysis).
7. **Validation report** (`validate.py`): prints row counts, event-type funnel %, top categories, long-tail plot stats, check that every column/enum matches the Data Contract, no broken foreign keys.
8. Load into DB using `common.db.load_csvs_to_db("data/raw")`.

## Part B — Ingest adapters for real platform data (`ingest/`, you own it)
Read `docs/DATA_STANDARD.md` fully. Build:
1. `ingest/transforms.py`: unix_ms/unix_s/date parsing → ISO, `money` (₹/$ strings → float ₹), rating rescale, `event_map`, `list_to_path`, `split_path`, `join_text`, `join_pipe`, HTML strip.
2. `ingest/run.py`: reads a YAML mapping (csv / jsonl / json, chunked), applies transforms, maps IDs to ours (saves `id_map`), fills defaults exactly as the "If missing" columns say, auto-builds sessions/orders/users when absent, drops PII, `--sample N` users, `--fill-missing` (uses data_gen to add offers/consent/missing user fields).
3. Validator (reuse `data_gen/validate.py`): detects data level L1–L4, writes `data/processed/<source>/report.md` (rows in/out, drop reasons, estimated fields, features ON/OFF).
4. Mappings in `ingest/mappings/`: `_template.yaml`, `amazon_reviews_2023.yaml`, `retailrocket.yaml`, `myntra_fashion.yaml`, and `synthetic.yaml` (proves our own data passes the same validator).
5. Tests with tiny hand-made raw files for each mapping (put fixtures in `tests/ingest/fixtures/`).

## Done when
- [ ] `python -m ingest.run --mapping ingest/mappings/retailrocket.yaml` works on the test fixture and loads into the DB; report shows correct data level
- [ ] Missing fields get the documented defaults; nothing crashes on L1-only data
- [ ] `python -m data_gen.generate --size small` runs in < 1 min; `--size full` produces ~1M events
- [ ] `python -m data_gen.validate` passes all checks (schema, enums, foreign keys, funnel rates sensible)
- [ ] Ground truth saved separately and NOT inside `data/raw/`
- [ ] Same seed → identical data
- [ ] `tests/data_gen` passes; `data_gen/README.md` written; Phase 1 ticked in `phases/PROGRESS.md`
