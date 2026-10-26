"""SQLite helpers: create tables, load CSVs, read tables, detect data level.

Usage:

    from common.db import get_connection, read_table, feature_enabled
    products = read_table("products")            # pandas DataFrame
    if feature_enabled("content"):               # skip features the data can't support
        ...

Table + column names come from the Data Contract in CLAUDE.md (TABLES below).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from common.config import PROJECT_ROOT, settings

# ---------------------------------------------------------------------------
# Schema: table -> list of (column, SQLite type). Order = column order in CSVs.
# Booleans are stored as INTEGER 0/1; timestamps as ISO-8601 TEXT; lists as "a|b|c" TEXT.
# ---------------------------------------------------------------------------
TABLES: dict[str, list[tuple[str, str]]] = {
    "users": [
        ("user_id", "TEXT PRIMARY KEY"), ("signup_date", "TEXT"), ("age_group", "TEXT"), ("gender", "TEXT"),
        ("city", "TEXT"), ("city_tier", "INTEGER"), ("device", "TEXT"), ("is_minor", "INTEGER DEFAULT 0"),
    ],
    "categories": [
        ("category_id", "TEXT PRIMARY KEY"), ("name", "TEXT"), ("parent_id", "TEXT"),
    ],
    "products": [
        ("product_id", "TEXT PRIMARY KEY"), ("title", "TEXT"), ("description", "TEXT"), ("category_id", "TEXT"),
        ("brand", "TEXT"), ("price", "REAL"), ("mrp", "REAL"), ("margin_pct", "REAL"), ("avg_rating", "REAL"),
        ("rating_count", "INTEGER"), ("stock_qty", "INTEGER"), ("launch_date", "TEXT"), ("tags", "TEXT"),
        ("is_consumable", "INTEGER DEFAULT 0"), ("repurchase_days", "INTEGER"), ("age_restricted", "INTEGER DEFAULT 0"),
    ],
    "sessions": [
        ("session_id", "TEXT PRIMARY KEY"), ("user_id", "TEXT"), ("start_time", "TEXT"), ("device", "TEXT"),
        ("is_gift", "INTEGER DEFAULT 0"),
    ],
    "events": [
        ("event_id", "TEXT PRIMARY KEY"), ("user_id", "TEXT"), ("product_id", "TEXT"), ("session_id", "TEXT"),
        ("event_type", "TEXT"), ("timestamp", "TEXT"), ("source", "TEXT"), ("search_query", "TEXT"),
    ],
    "orders": [
        ("order_id", "TEXT PRIMARY KEY"), ("user_id", "TEXT"), ("order_date", "TEXT"), ("total_amount", "REAL"),
    ],
    "order_items": [
        ("order_id", "TEXT"), ("product_id", "TEXT"), ("quantity", "INTEGER"), ("price_paid", "REAL"),
    ],
    "reviews": [
        ("review_id", "TEXT PRIMARY KEY"), ("user_id", "TEXT"), ("product_id", "TEXT"), ("rating", "INTEGER"),
        ("review_text", "TEXT"), ("review_date", "TEXT"),
    ],
    "offers": [
        ("offer_id", "TEXT PRIMARY KEY"), ("code", "TEXT"), ("offer_type", "TEXT"), ("value", "REAL"),
        ("min_cart_value", "REAL DEFAULT 0"), ("category_id", "TEXT"), ("product_id", "TEXT"),
        ("bundle_product_id", "TEXT"), ("segment", "TEXT"), ("trigger_type", "TEXT"),
        ("max_uses_per_user", "INTEGER DEFAULT 1"), ("start_at", "TEXT"), ("expires_at", "TEXT"),
        ("is_active", "INTEGER DEFAULT 1"), ("requires_approval", "INTEGER DEFAULT 0"),
    ],
    "offer_rankings": [
        ("user_id", "TEXT"), ("offer_id", "TEXT"), ("score", "REAL"), ("rank", "INTEGER"), ("created_at", "TEXT"),
    ],
    "offer_sends": [
        ("send_id", "TEXT PRIMARY KEY"), ("user_id", "TEXT"), ("offer_id", "TEXT"), ("trigger_type", "TEXT"),
        ("message", "TEXT"), ("channel", "TEXT"), ("status", "TEXT"), ("sent_at", "TEXT"), ("updated_at", "TEXT"),
    ],
    "user_consent": [
        ("user_id", "TEXT PRIMARY KEY"), ("opted_in", "INTEGER DEFAULT 0"), ("quiet_start", "TEXT"),
        ("quiet_end", "TEXT"), ("preferred_categories", "TEXT"), ("max_offers_per_week", "INTEGER"),
    ],
    "telegram_links": [
        ("user_id", "TEXT PRIMARY KEY"), ("chat_id", "TEXT"), ("link_token", "TEXT UNIQUE"), ("linked_at", "TEXT"),
        ("active", "INTEGER DEFAULT 0"),
    ],
    "audit_log": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"), ("timestamp", "TEXT"), ("component", "TEXT"), ("user_id", "TEXT"),
        ("action", "TEXT"), ("decision", "TEXT"), ("reason", "TEXT"), ("details_json", "TEXT"),
    ],
    "id_map": [
        ("entity", "TEXT"), ("internal_id", "TEXT"), ("source", "TEXT"), ("external_id", "TEXT"),
    ],
}

# Indexes make the common lookups ("all events of this user") fast.
INDEXES: list[tuple[str, str]] = [
    ("products", "category_id"),
    ("sessions", "user_id"),
    ("events", "user_id"), ("events", "product_id"), ("events", "timestamp"), ("events", "session_id"),
    ("orders", "user_id"),
    ("order_items", "order_id"), ("order_items", "product_id"),
    ("reviews", "product_id"),
    ("offer_rankings", "user_id"),
    ("offer_sends", "user_id"), ("offer_sends", "sent_at"),
    ("audit_log", "user_id"),
    ("id_map", "internal_id"),
]

# Ground truth = evaluation only, NEVER a model input. Kept as CSVs, not loaded into app.db.
GROUND_TRUTH_TABLES = ("user_hidden_profile", "product_relations")


def columns(table: str) -> list[str]:
    """Column names of a contract table, in order."""
    _check_table(table)
    return [name for name, _ in TABLES[table]]


def _check_table(table: str) -> None:
    # Only known names are allowed, so a table name can never inject SQL.
    if table not in TABLES:
        raise ValueError(f"Unknown table '{table}'. Known tables: {', '.join(TABLES)}")


# ---------------------------------------------------------------------------
# Connection + schema
# ---------------------------------------------------------------------------


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open the SQLite database (creates the file + folder if needed).

    Use it as a context manager so changes are saved automatically:
        with get_connection() as conn:
            conn.execute("UPDATE ...")
    """
    path = Path(db_path) if db_path else settings.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # timeout: wait up to 30 s if another process (e.g. the bot) is writing at the same moment.
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row  # rows behave like dicts: row["user_id"]
    return conn


def init_db(db_path: str | Path | None = None) -> None:
    """Create every Data Contract table and index (safe to run many times)."""
    with get_connection(db_path) as conn:
        for table, cols in TABLES.items():
            col_sql = ", ".join(f"{name} {sql_type}" for name, sql_type in cols)
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({col_sql})")
        for table, col in INDEXES:
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_{col} ON {table} ({col})")
        # One link per external ID per source.
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_id_map_unique ON id_map (entity, source, external_id)")


# ---------------------------------------------------------------------------
# Data folders
# ---------------------------------------------------------------------------


def data_folder(source: str | None = None) -> Path:
    """Folder with the Standard Format CSVs for DATA_SOURCE (sample | synthetic | external)."""
    source = source or settings.DATA_SOURCE
    if source == "sample":
        return PROJECT_ROOT / "data" / "sample"
    if source == "synthetic":
        return PROJECT_ROOT / "data" / "raw"
    if source == "external":
        if not settings.EXTERNAL_SOURCE:
            raise ValueError("DATA_SOURCE=external needs EXTERNAL_SOURCE=<folder in data/processed/> in .env")
        return PROJECT_ROOT / "data" / "processed" / settings.EXTERNAL_SOURCE
    raise ValueError(f"Unknown DATA_SOURCE '{source}' (use sample, synthetic or external)")


def ground_truth_folder(source: str | None = None) -> Path | None:
    """Folder with ground-truth CSVs, or None (real external data has no ground truth)."""
    source = source or settings.DATA_SOURCE
    if source == "sample":
        return PROJECT_ROOT / "data" / "sample" / "ground_truth"
    if source == "synthetic":
        return PROJECT_ROOT / "data" / "ground_truth"
    return None


def read_ground_truth(name: str, source: str | None = None) -> pd.DataFrame | None:
    """Read a ground-truth CSV (evaluation only!). Returns None if it doesn't exist."""
    if name not in GROUND_TRUTH_TABLES:
        raise ValueError(f"Unknown ground-truth table '{name}'. Known: {', '.join(GROUND_TRUTH_TABLES)}")
    folder = ground_truth_folder(source)
    if folder is None or not (folder / f"{name}.csv").exists():
        return None
    return pd.read_csv(folder / f"{name}.csv", dtype=str, keep_default_na=False)


# ---------------------------------------------------------------------------
# Loading + reading
# ---------------------------------------------------------------------------


def load_csvs_to_db(folder: str | Path | None = None, db_path: str | Path | None = None) -> dict[str, int]:
    """Load every `<table>.csv` found in `folder` into the DB, replacing that table's rows.

    Tables without a CSV are left untouched. Extra CSV columns are ignored; missing
    optional columns become NULL. Returns {table: rows_loaded}.
    """
    folder = Path(folder) if folder else data_folder()
    if not folder.is_absolute():
        folder = PROJECT_ROOT / folder
    if not folder.exists():
        raise FileNotFoundError(f"Data folder not found: {folder}")

    init_db(db_path)
    loaded: dict[str, int] = {}
    with get_connection(db_path) as conn:
        for table in TABLES:
            csv_path = folder / f"{table}.csv"
            if not csv_path.exists():
                continue
            # dtype=str keeps IDs exactly as written; SQLite turns numeric text into numbers
            # for REAL/INTEGER columns. Empty cells become NaN -> NULL.
            df = pd.read_csv(csv_path, dtype=str)
            cols = [c for c in columns(table) if c in df.columns]
            rows = df[cols].astype(object).where(df[cols].notna(), None).values.tolist()
            conn.execute(f"DELETE FROM {table}")
            placeholders = ", ".join("?" for _ in cols)
            conn.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})", rows)
            loaded[table] = len(rows)
    return loaded


def read_table(name: str, db_path: str | Path | None = None) -> pd.DataFrame:
    """Return a whole table as a pandas DataFrame (empty DataFrame if the table has no rows)."""
    _check_table(name)
    with get_connection(db_path) as conn:
        return pd.read_sql_query(f"SELECT * FROM {name}", conn)


def table_counts(db_path: str | Path | None = None) -> dict[str, int]:
    """Row count per contract table (-1 if the table doesn't exist yet)."""
    counts: dict[str, int] = {}
    with get_connection(db_path) as conn:
        existing = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in TABLES:
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if table in existing else -1
    return counts


# ---------------------------------------------------------------------------
# Data levels (docs/DATA_STANDARD.md §1) and feature switches
# ---------------------------------------------------------------------------

# Tables that must have rows to reach each level (each level includes the ones before).
# offer_sends is not required for L4: a fresh system simply hasn't sent anything yet.
LEVEL_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "L1": ("events",),
    "L2": ("products",),
    "L3": ("users", "orders", "reviews", "sessions"),
    "L4": ("offers", "user_consent"),
}

# Feature name -> minimum data level it needs.
FEATURE_LEVELS: dict[str, str] = {
    # L1 — interactions only
    "popularity": "L1", "item_cf": "L1", "als": "L1", "evaluation": "L1",
    # L2 — + product catalog
    "content": "L2", "cold_start": "L2", "similar_items": "L2", "budget_aware": "L2",
    "explanations": "L2", "reranking": "L2", "ranker": "L2",
    # L3 — + users, orders, reviews, sessions
    "demographics": "L3", "post_purchase": "L3", "repurchase": "L3", "gift_mode": "L3",
    # L4 — + offers and consent
    "offers": "L4", "uplift": "L4", "telegram": "L4",
}


def get_data_level(db_path: str | Path | None = None) -> str:
    """Highest data level ('L0'–'L4') the loaded data supports. 'L0' = no events at all."""
    counts = table_counts(db_path)
    level = "L0"
    for name, required in LEVEL_REQUIREMENTS.items():
        if all(counts.get(t, -1) > 0 for t in required):
            level = name
        else:
            break  # levels build on each other, so stop at the first missing one
    return level


def feature_enabled(name: str, db_path: str | Path | None = None) -> bool:
    """True if the current data supports feature `name` (see FEATURE_LEVELS).

    Special case: 'ground_truth' is on only when ground-truth CSVs exist (sample/synthetic data).
    Unknown feature names raise an error so typos are caught early.
    """
    if name == "ground_truth":
        folder = ground_truth_folder()
        return folder is not None and all((folder / f"{t}.csv").exists() for t in GROUND_TRUTH_TABLES)
    if name not in FEATURE_LEVELS:
        raise ValueError(f"Unknown feature '{name}'. Known: ground_truth, {', '.join(FEATURE_LEVELS)}")
    return get_data_level(db_path) >= FEATURE_LEVELS[name]  # 'L3' >= 'L2' works as text comparison
