# Standard Data Format (works with our data AND real platform data)

**Goal:** the whole system (reco engine, offers, bot, UI) reads only ONE format — the **Standard Format** below.
Any data source — our synthetic generator, an Amazon/Myntra/Flipkart public dataset, a store's CSV export — is converted into it by an **adapter**. So testing on real data = write a small mapping file, run one command.

```
Synthetic generator ─────────────────────────────┐
Amazon dataset ──► adapter (mapping .yaml) ──┐   │
Myntra dataset ──► adapter (mapping .yaml) ──┼──►│ Standard Format ─► Validator ─► data/app.db ─► whole system
Any CSV/JSON ────► adapter (mapping .yaml) ──┘   │   (Data Contract in CLAUDE.md)
```

---

## 1. Data levels — what you need vs. what's optional

Real datasets rarely have everything. The system works with whatever level is available and **switches off** features that need missing data (never crashes).

| Level | Required tables | Features that work |
|---|---|---|
| **L1 — Minimum** | `events` (user, product, timestamp, event type *or* rating) | Popularity, item-item CF, ALS, Recall/NDCG evaluation |
| **L2 — + Catalog** | L1 + `products` (id, title; price/category/brand if available) | + content-based, cold start, similar items, budget-aware, explanations, re-ranking |
| **L3 — Full** | L2 + `users`, `orders`, `reviews`, `sessions` | + demographics, post-purchase intelligence, repurchase, gift mode |
| **L4 — Offers** | L3 + `offers`, `user_consent`, `offer_sends` | + Offer pipeline, uplift analysis, Telegram |

Real public datasets are usually **L1–L2**. Our synthetic data is **L4**. For L4 features on real data, the missing tables (offers, consent) are filled from our generator.

---

## 2. Standard fields (required ✅ / optional ◻ / default if missing)

Column names and enums = **Data Contract in `CLAUDE.md`**. This table adds the rules for external data.

### events
| Field | Req | Type | If missing |
|---|---|---|---|
| user_id | ✅ | string | — (row dropped) |
| product_id | ✅* | string | *may be empty only for `search` |
| timestamp | ✅ | ISO-8601 | — (row dropped) |
| event_type | ✅ | enum | Derived: review/rating/order → `purchase`; else `view` |
| session_id | ◻ | string | Built automatically: same user, gap < 30 min = same session |
| source | ◻ | enum | `home` |
| search_query | ◻ | string | empty |
| event_id | ◻ | string | Auto-generated |

### products
| Field | Req | Type | If missing |
|---|---|---|---|
| product_id | ✅ | string | — |
| title | ✅ | string | `"Product <id>"` |
| category_id | ◻ | string | Built from category path (e.g. `Clothing > Men > T-shirts`) → category tree; else `UNKNOWN` |
| brand | ◻ | string | `Unknown` |
| price | ◻ | float ₹ | Median price of its category (flag estimated) |
| mrp | ◻ | float ₹ | = price |
| margin_pct | ◻ | float | Category default from `docs/DATA_PARAMETERS.md` (flag estimated) |
| description, tags | ◻ | text | empty (content model uses title only) |
| avg_rating, rating_count | ◻ | float/int | Computed from reviews if present; else empty |
| stock_qty | ◻ | int | 100 (assume in stock) |
| launch_date | ◻ | date | First seen event date |
| is_consumable, repurchase_days | ◻ | bool/int | From category defaults (Grocery/Beauty = consumable) |
| age_restricted | ◻ | bool | 0 |

### users
| Field | Req | If missing |
|---|---|---|
| user_id | ✅ | Created from distinct user_ids in events |
| signup_date | ◻ | First event date |
| age_group, gender, city, city_tier, device | ◻ | `unknown` (features using them are skipped) |
| is_minor | ◻ | 0 |

### orders / order_items / reviews
- **orders/order_items** missing → built from `purchase` events (1 order per user per day).
- **reviews** — a review with a rating also creates a `purchase` event (people review what they bought).

### Tables that real datasets never have
`offers`, `user_consent`, `offer_sends`, `telegram_links`, `offer_rankings`, `audit_log` → created empty or filled by our generator (`--fill-missing` flag).

---

## 3. Normalization rules (applied by every adapter)

| Problem in raw data | Rule |
|---|---|
| IDs like `B07XJ8C8F5`, `visitor 12345` | Mapped to our IDs (`P00001`, `U00001`). Original kept in `id_map` table (entity, internal_id, source, external_id) |
| Timestamps as Unix seconds/ms, `"12-08-2026"`, etc. | → ISO-8601 in `TIMEZONE` |
| Prices like `"₹1,299"`, `"$12.99"`, `"1299.00"` | → float ₹. Other currencies × rate from mapping file |
| Ratings 1–10 or 0–5 | → 1–5 scale |
| Event names `addtocart`, `transaction`, `click`, `buy` | → our enum via `event_map` in the mapping file |
| Category as list / path / string | → category tree + `category_id` of the deepest level |
| Duplicates | Same user + product + type + timestamp → keep one |
| Text with HTML | Strip tags, trim to 2,000 chars |
| Bad rows (no user/product/time) | Dropped and counted in the validation report |
| Huge files | Read in chunks; `--sample N` keeps N random users for fast testing |

---

## 4. Mapping file (one per source)

Location: `ingest/mappings/<source>.yaml`. You only describe *which raw column becomes which standard field*.

### Example A — Amazon Reviews 2023 (McAuley Lab, public, L2: reviews + metadata)
```yaml
source: amazon_reviews_2023
currency: USD
usd_to_inr: 84            # update to current rate
files:
  events:
    path: data/external/amazon/Electronics.jsonl
    format: jsonl
    map:
      user_id: user_id
      product_id: parent_asin
      timestamp: {field: timestamp, transform: unix_ms}
      event_type: {const: purchase}          # each review = a purchase
  reviews:
    path: data/external/amazon/Electronics.jsonl
    format: jsonl
    map:
      user_id: user_id
      product_id: parent_asin
      rating: rating
      review_text: text
      review_date: {field: timestamp, transform: unix_ms}
  products:
    path: data/external/amazon/meta_Electronics.jsonl
    format: jsonl
    map:
      product_id: parent_asin
      title: title
      brand: store
      price: {field: price, transform: money}
      category_path: {field: categories, transform: list_to_path}
      description: {field: description, transform: join_text}
      tags: {field: features, transform: join_pipe}
      avg_rating: average_rating
      rating_count: rating_number
```

### Example B — RetailRocket (public, L1 with real funnel events)
```yaml
source: retailrocket
files:
  events:
    path: data/external/retailrocket/events.csv
    format: csv
    map:
      user_id: visitorid
      product_id: itemid
      timestamp: {field: timestamp, transform: unix_ms}
      event_type: {field: event, transform: event_map}
    event_map: {view: view, addtocart: add_to_cart, transaction: purchase}
```

### Example C — Myntra-style fashion catalog (Kaggle, L2 catalog)
> Column names differ between Kaggle uploads — open the CSV, check headers, adjust the left side.
```yaml
source: myntra_fashion
currency: INR
files:
  products:
    path: data/external/myntra/products.csv
    format: csv
    map:
      product_id: p_id            # ← change to the real column name
      title: name
      brand: brand
      price: {field: price, transform: money}
      category_path: {field: category, transform: split_path, sep: ">"}
      description: description
      avg_rating: avg_rating
      rating_count: ratingCount
```
A catalog-only dataset has no users/events → combine it with our generator: `--fill-missing` simulates shoppers **on the real catalog** (best of both: real products, simulated behaviour + ground truth).

### Example D — Any store's own export (generic CSV)
Copy `ingest/mappings/_template.yaml`, fill the right-hand column names, done.

---

## 5. How to run

```powershell
# 1. Put raw files in data/external/<source>/   (gitignored)
# 2. Convert + validate + load
python -m ingest.run --mapping ingest/mappings/amazon_reviews_2023.yaml --sample 5000
#    optional: --fill-missing   (generate offers/consent/users fields with our generator)
# 3. Point the system at it
#    .env → DATA_SOURCE=external  (or synthetic / sample)
python -m reco.train
python -m reco.evaluate
```

Output: standard CSVs in `data/processed/<source>/` + loaded into `data/app.db` + **validation report** (`data/processed/<source>/report.md`) showing: rows in/out, dropped rows and why, data level detected (L1–L4), which features are ON/OFF, estimated fields.

---

## 6. Rules & good practice
- **Never scrape Amazon/Myntra/Flipkart websites** — it breaks their terms of service. Use official public datasets (Amazon Reviews 2023, RetailRocket, H&M, Kaggle uploads with a clear licence) or a partner's own export. Check each dataset's licence; cite it in the report.
- Raw external data stays in `data/external/` (gitignored, not shared publicly).
- Real data has **no ground truth** → ground-truth metrics are skipped; Recall/NDCG/coverage still work.
- Personal data in real datasets (names, emails) must be dropped by the adapter — we only need anonymous IDs.
