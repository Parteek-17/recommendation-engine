# ingest/ — adapters for real platform data (Phase 1, Part B)

Converts any dataset (Amazon Reviews 2023, RetailRocket, a Myntra catalog, a store's CSV export)
into the **Standard Format** that the whole system reads, validates it, writes a report and loads
it into the DB. Spec: `docs/DATA_STANDARD.md`.

## Run

```powershell
# 1. put the raw files in data/external/<source>/   (gitignored, never committed)
# 2. convert + validate + load
python -m ingest.run --mapping ingest/mappings/retailrocket.yaml --sample 5000
python -m ingest.run --mapping ingest/mappings/amazon_reviews_2023.yaml --sample 5000
python -m ingest.run --mapping ingest/mappings/myntra_fashion.yaml --fill-missing
# 3. point the system at it: .env -> DATA_SOURCE=external, EXTERNAL_SOURCE=<source>
```

| Option | Meaning |
|---|---|
| `--input DIR` | read the raw files from DIR instead of the folders in the mapping (same file names) |
| `--out DIR` | output folder (default `data/processed/<source>/`) |
| `--sample N` | keep N random users (seeded) — fast tests on huge files |
| `--fill-missing` | add offers + consent + demographics from our generator; a catalog-only dataset gets **simulated shoppers on the real catalog** (with ground truth in `<out>/ground_truth/`) |
| `--db PATH` / `--no-db` | load into another DB file / don't load at all |

Try it on the test fixtures without touching anything shared:

```powershell
python -m ingest.run --mapping ingest/mappings/retailrocket.yaml --input tests/ingest/fixtures/retailrocket --out data/processed/rr_test --db data/processed/rr_test/test.db
```

Output in `data/processed/<source>/`: all 15 contract CSVs (empty ones = header only, so loading
replaces old rows), `id_map.csv` (our id ↔ original id) and **`report.md`**: rows in/out, dropped
rows and why, estimated/default values, what was built automatically, the data level (L1-L4),
features ON/OFF, licence.

## Files

| File | What |
|---|---|
| `transforms.py` | `unix_ms`, `unix_s`, `date` (day-first, tz-aware), `money` (₹/$/Rs strings → ₹), `rating` (rescale to 1-5), `event_map`, `list_to_path`, `split_path`, `join_text`, `join_pipe`, `strip_html` |
| `run.py` | the converter (read in chunks → map → transform → clean → ids → build missing tables → defaults → write → validate → load) |
| `mappings/_template.yaml` | copy this for a new source (documents every option) |
| `mappings/retailrocket.yaml` | events only → **L1** |
| `mappings/amazon_reviews_2023.yaml` | reviews + catalog → **L3** (users/orders/sessions built from reviews) |
| `mappings/myntra_fashion.yaml` | catalog only → **L0** alone, **L4** with `--fill-missing` |
| `mappings/synthetic.yaml` | our own `data/raw/` through the same validator (`keep_ids: true`, `map: same`) → **L4** |

## Rules it applies (from DATA_STANDARD "If missing")

- Rows without user / time (or product, except searches), unknown event names and duplicates
  (same user + product + type + time) are **dropped and counted**.
- No event type → `purchase` if the row has a rating, else `view`. Each rated review also becomes a
  `purchase` event. No sessions → same user with < 30 min gap = one session. No orders → 1 order per
  user per day from purchase events. No users → created from the ids seen (signup = first event).
- Products: title `Product <id>`, brand `Unknown`, category from the path (deepest level; `UNKNOWN`
  if none), price = median of its category (then parent, then overall), mrp = price, margin and
  consumable from the parent-category defaults, stock 100, launch = first seen, ratings from reviews.
  Only products somebody interacted with are kept when interaction data exists.
- Users: `unknown` demographics (city_tier stays empty because it is a number column).
- All times become ISO-8601 local time (`TIMEZONE`) without an offset; HTML stripped, text ≤ 2,000 chars.
- **Personal data**: only mapped Standard fields are kept; raw columns that look personal are listed
  in the report as "not copied"; ids that look like emails/phone numbers are stored hashed in `id_map`.
- Never scrape websites — use official dataset downloads and respect their licence (put it in the mapping's `licence`).

## Test

```powershell
pytest tests/ingest          # fixtures in tests/ingest/fixtures/ (one per mapping)
```
