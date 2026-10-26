# data_gen/ — synthetic data generator (Phase 1)

Creates a realistic **Indian general-marketplace** dataset (₹ prices, 8 parent / 25 sub-categories)
with **planted patterns** that our models should discover, plus the hidden **ground truth** used to
check them. Output = the Standard Format (Data Contract in `CLAUDE.md`), data level **L4**.

## Run

```powershell
python -m data_gen.generate --size small     # 1,000 users · 500 products · ~95k events · ~15 s
python -m data_gen.generate --size full      # 10,000 users · 3,000 products · ~1M events · ~2-3 min
python -m data_gen.validate                  # re-check data/raw/ (+ data/ground_truth/)
```

Options: `--seed 7` (other data), `--no-db` (CSVs only), `--db path.db` (load somewhere else),
`--out` / `--ground-truth` (other folders). Sizes and every probability live in `data_gen/config.py`.

| Output | What |
|---|---|
| `data/raw/*.csv` | all 15 contract tables (empty ones = header only, e.g. `telegram_links`, `offer_rankings`) |
| `data/ground_truth/user_hidden_profile.csv` | hidden taste per user — **evaluation only** |
| `data/ground_truth/product_relations.csv` | complements / substitutes — **evaluation only** |
| DB (`DB_PATH`) | loaded with `common.db.load_csvs_to_db("data/raw")` unless `--no-db` |

After generating, set `DATA_SOURCE=synthetic` in `.env` so every module reads this data
(`python scripts/init_db.py --fresh` reloads it any time).

## Files (one per step)

| File | Step |
|---|---|
| `config.py` | every knob (sizes, rates, multipliers, sale windows, seed) from `docs/DATA_PARAMETERS.md` |
| `templates.py` | category tree, brands, product types, complement rules, search & review texts |
| `catalog.py` | 1 — categories + products (titles from brand + type + attributes, prices, ratings, stock, launch dates) |
| `relations.py` | 2 — ground-truth complements (rules) and substitutes (same sub-category + kind, price ±30%) |
| `users.py` | 3 — users + hidden profiles (2-3 preferred categories, price sensitivity, activity, gifter) |
| `simulate.py` | 4 — day-by-day behaviour simulation |
| `orders.py` | 5 — orders, order_items, reviews from purchases |
| `offers.py` | 6 — ~55 offers, consent, simulated offer responses (`OfferSimulator`) |
| `validate.py` | 7 — validation report (also used by `ingest/`) |
| `generate.py` | runs everything + step 8 (load into DB) |
| `utils.py` | seeded random streams, dates, CSV writing |

## Planted patterns (what models should find)

| Pattern | How it is planted |
|---|---|
| Taste | 80% of browsing in the user's 2-3 hidden preferred categories; funnel rates ×2 there |
| Budget | each user has a comfort zone inside every category's price range (from price sensitivity); ×1.5 inside, ×0.4 outside |
| Long tail | Zipf popularity (exponent 1.1): top 20% of products ≈ 66% (small) / 78% (full) of views |
| Post-purchase | after a non-consumable: the item + its substitutes ×0.1 interest for 60 days; complements ×3 for 14 days; 25% come back to buy one; extra basket items are complements first |
| Repurchase | consumables re-bought at `repurchase_days` ± 20% (60%); the other 40% become `repurchase_due` triggers |
| Calendar | weekends +20% traffic, evening peak 7-11 PM, sale Aug 10-15: traffic ×2, conversion ×1.8; 10 viral products for 2-3 weeks |
| Gift sessions | 5% of a gifter's sessions, `is_gift=1`, browse gift categories outside their taste |
| Cold start | ~10% of products launch in the last 6 weeks; ~10% of users sign up in the last month |
| Safety | minors never see age-restricted products; minors are never opted in to offers |
| Offers + uplift | from July, triggers (cart abandon, price drop, post-purchase, repurchase due, win-back, festival) → consent + weekly cap → offer → sent/opened/clicked/redeemed. 20% of eligible triggers are a **holdout** (nothing sent) that still buys at 20% of the treated redemption rate → true uplift ≈ 80% of redemptions. Redeemed purchases have `source=telegram` and a discounted `price_paid`. |

Measured on `--size full` (seed 42): ~1M events, view→cart ≈ 14-15%, cart→purchase ≈ 40%,
substitute views drop ~3× after a purchase while complement views rise ~2.5×.

## Choices worth knowing

- **Activity scale**: the activity ranges (light 1-2, regular 4-8, heavy 15-30 sessions/month) are
  relative; `activity_scale` in `SIZES` tunes the total to ~1M events for `full`.
- **Softened multipliers**: clicks, wishlists and purchases use `sqrt(m / 2)` of the taste × comfort ×
  rating multiplier so realised rates stay near the base rates; add-to-cart uses the full multiplier.
- **Substitutes** additionally require the same product *kind* (a tripod is not a substitute for a
  memory card even though both are "Cameras & Photography").
- **Guardrail test offers** are included on purpose: `MEGA50` (50% > MAX_DISCOUNT_PCT), `SUMMER20`
  (expired), `OLDDROP10` (inactive), `FESTIVE1500` (needs approval). The simulated pipeline never sends them.
- **Determinism**: every step has its own seeded random stream (`utils.make_rngs`), so the same seed
  gives byte-identical CSVs (same Python/pandas/Faker versions).

## Test

```powershell
pytest tests/data_gen        # ~10 s, uses a 'tiny' size (200 users, 150 products)
```
