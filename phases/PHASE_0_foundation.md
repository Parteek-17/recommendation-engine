# PHASE 0 — Foundation (run FIRST, alone)

> Paste everything below into a new chat with the project folder selected.

---

You are building **Phase 0 (Foundation)** of the E-commerce Recommendation Engine.

**Before anything:** read `CLAUDE.md` completely. It is the source of truth (data contract, module contracts, folder structure, rules). I am a beginner — explain each step in 1–2 lines as you go and keep code simple and commented.

## Goal
Create the shared skeleton so that Phases 1–6 can be built **in parallel** without waiting for each other.

## You own
`common/`, `data/sample/`, `scripts/`, root files (`README.md`, `requirements.txt`, `.env.example`, `.gitignore`), empty module folders with `__init__.py`, `tests/common/`.

## Build
1. **Folder structure** exactly as in `CLAUDE.md` (create every module folder with an empty `__init__.py`, plus `tests/<module>/`).
2. **`requirements.txt`** with the tech stack from `CLAUDE.md` (pin major versions; group with comments per module).
3. **`.env.example`** with: `ANTHROPIC_API_KEY=`, `TELEGRAM_BOT_TOKEN=`, `TELEGRAM_BOT_USERNAME=`, `OFFERS_API_KEY=change-me`, `TIMEZONE=Asia/Kolkata`, `CLAUDE_MODEL_FAST=`, `CLAUDE_MODEL_SMART=`, `DB_PATH=data/app.db`, `DATA_SOURCE=sample` (sample|synthetic|external), `OFFERS_API_URL=http://127.0.0.1:8001`, `MAIN_API_URL=http://127.0.0.1:8000`, `EXTERNAL_SOURCE=` (name of folder in data/processed/), plus guardrail limits (`MAX_DISCOUNT_PCT=40`, `MAX_OFFERS_PER_DAY=1`, `MAX_OFFERS_PER_WEEK=3`, `QUIET_START=22:00`, `QUIET_END=08:00`, `APPROVAL_THRESHOLD_INR=1000`, `DAILY_DISCOUNT_BUDGET_INR=50000`). Add a comment above each line explaining it.
4. **`.gitignore`**: `.env`, `.venv/`, `__pycache__/`, `data/app.db`, `data/raw/`, `data/external/`, `data/processed/`, `models/`, `*.log`.
5. **`common/config.py`**: loads `.env` with python-dotenv, exposes a `settings` object; missing keys give a **friendly error message** pointing to `docs/SETUP_GUIDE.md`; never prints secret values (mask as `sk-...xyz`).
6. **`common/schemas.py`**: pydantic models from the Module Contracts (`RecItem`, `Offer`, `RankedOffer`, `UserContext`, `ChatReply`, `GuardrailResult`) + enums from the Data Contract.
7. **`common/db.py`**: SQLite helpers — `get_connection()`, `init_db()` (creates all tables in the Data Contract with sensible types/indexes), `load_csvs_to_db(folder)`, `read_table(name) -> DataFrame`. Creates the `id_map` table too. `DATA_SOURCE` picks the folder: `sample` → `data/sample/`, `synthetic` → `data/raw/`, `external` → `data/processed/<EXTERNAL_SOURCE>/`. Also add `get_data_level()` that reports L1–L4 (see `docs/DATA_STANDARD.md`) and `feature_enabled(name)` so modules can switch features off when data is missing.
8. **`common/logging_utils.py`**: simple logger + `audit(component, user_id, action, decision, reason, details)` that writes to `audit_log`.
9. **`common/stubs.py`**: fake but valid implementations of EVERY function in the Module Contracts (return realistic dummy data using sample data). Other phases import these until real modules exist.
10. **`data/sample/`**: tiny hand-crafted but realistic fixture CSVs for every table (≈50 users, 100 products, 8 categories, 2,000 events, 20 offers, ground-truth files). Include at least: one complement pair (phone→cover), one substitute pair, one consumable with `repurchase_days`, one gift session, one new user, one age-restricted product. Generate with a small script `scripts/make_sample_data.py` (seeded, reproducible).
11. **`scripts/init_db.py`**: creates `data/app.db` and loads the sample CSVs.
12. **`scripts/check_setup.py`**: health check that prints ✅/❌ for: Python version, packages installed, `.env` exists, each key present (masked), DB exists, Anthropic key works (tiny test call, optional flag `--online`), Telegram token works (`getMe`, optional).
13. **Root `README.md`**: 10-line quick start (setup → init db → run check) linking to `docs/`.
14. **Tests** in `tests/common/`: schemas validate, db init creates all tables, stubs return correct types.

## Done when
- [ ] `python scripts/init_db.py` creates the DB with all tables loaded from sample data
- [ ] `python scripts/check_setup.py` runs and gives clear ✅/❌ output
- [ ] `pytest tests/common` passes
- [ ] Every contract function exists in `common/stubs.py`
- [ ] Phase 0 ticked in `phases/PROGRESS.md`

Finish with a short summary of what was created and how other phases should import `common`.
