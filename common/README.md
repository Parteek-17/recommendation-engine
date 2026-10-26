# common/ — shared foundation (Phase 0)

Code every module imports. **Owned by Phase 0 — other phases must not edit it.** Need a change? Write it in `phases/NOTES.md`.

| File | What it gives you |
|---|---|
| `config.py` | `settings` (all `.env` values, typed), `settings.require("KEY")` (friendly error if missing), `mask()` |
| `schemas.py` | Pydantic models `RecItem`, `Offer`, `RankedOffer`, `UserContext`, `ChatReply`, `GuardrailResult`; enums `EventType`, `EventSource`, `OfferType`, `TriggerType`, `SendStatus`, `Relation`; `EVENT_WEIGHTS`; time-split dates |
| `db.py` | `get_connection()`, `init_db()`, `load_csvs_to_db(folder)`, `read_table(name)`, `table_counts()`, `data_folder()`, `read_ground_truth(name)`, `get_data_level()`, `feature_enabled(name)`, `TABLES` (the schema) |
| `logging_utils.py` | `get_logger(__name__)`, `audit(component, user_id, action, decision, reason, details)`, `now_local()`, `now_iso()` |
| `stubs.py` | Fake-but-valid versions of every Module Contract function (reco, offers, llm, bot, pipeline) |

## How to use it

```python
from common.config import settings
from common.db import read_table, feature_enabled, get_connection
from common.schemas import RecItem, Offer, UserContext, EventType
from common.logging_utils import get_logger, audit, now_local

log = get_logger(__name__)
events = read_table("events")                      # pandas DataFrame
if feature_enabled("content"):                     # off when the data has no product catalog
    ...
audit("offers", "U00001", "send_offer", "blocked", "frequency cap reached", {"offer_id": "OF0001"})
```

**Using another module before it exists** — import the stub, swap later:

```python
try:
    from reco import get_recommendations           # real module (once Phase 2 exports it)
except ImportError:
    from common.stubs import get_recommendations   # fake but valid
```

Phases should export their contract functions from their package `__init__.py` (e.g. `reco/__init__.py`) so the `try/except` above switches over automatically.

## Conventions worth knowing

- **Timestamps** are local time in `TIMEZONE` **without** an offset, e.g. `2026-09-01T19:30:00`. They sort/compare correctly as text. Use `now_local()` / `now_iso()`, never `datetime.now()` / `utcnow()`.
- **Booleans** are stored as `0/1`. **Lists** are pipe-separated (`C009|C010`).
- **Ground truth** (`user_hidden_profile`, `product_relations`) is **not** loaded into `app.db` — read it only in evaluation code with `read_ground_truth()`. Never use it as a model input.
- **Data level**: `get_data_level()` returns `L0`–`L4` (see `docs/DATA_STANDARD.md`). L4 needs rows in `offers` + `user_consent`; `offer_sends` may be empty. Feature names for `feature_enabled()` are in `FEATURE_LEVELS` in `db.py` (plus `ground_truth`).
- Tests should use a temp DB: pass `db_path=` to the `db` functions (see `tests/conftest.py` fixtures `tmp_db`, `sample_db`).

## Sample data (`data/sample/`)

Made by `python scripts/make_sample_data.py` (seed 42, identical every run): 50 users, 100 products, 8 parent + 16 sub-categories, ~2,160 events, 20 offers, ground truth in `data/sample/ground_truth/`.

Planted test cases:

| Case | Where |
|---|---|
| Complement (phone → cover) | `P00001` Samsung phone → `P00002` Spigen cover; U00001 buys both, 2 days apart |
| Substitute | `P00001` ↔ `P00003` Redmi phone (similar price, same sub-category) |
| Repurchase | `P00004` Aashirvaad Atta, `repurchase_days=30`; U00002 buys it every ~30 days |
| Age-restricted | `P00005` Chef Knife Set + "Party Card Game (18+)" products; U00007 is a minor |
| Gift session | U00003, 2026-08-12, `is_gift=1`, buys toys |
| Cart abandon | U00004 adds shoes to cart on 2026-09-28, never buys |
| Cold-start user | U00050 signed up 2026-09-28, no events |
| Cold-start items | ~10% of products launched after 2026-08-20 |
| Guardrail offers | `OF0017` 50% off (> MAX_DISCOUNT_PCT), `OF0018` expired, `OF0019` inactive, `OF0013` needs approval |
| Telegram | U00001–U00003 linked; U00004 has a token but never pressed Start |

## Run / test

```powershell
python scripts/make_sample_data.py   # regenerate sample CSVs
python scripts/init_db.py --fresh    # rebuild data/app.db from DATA_SOURCE
python scripts/check_setup.py        # health check
pytest tests/common                  # 27 tests
```
