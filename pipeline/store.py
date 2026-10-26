"""Small DB helpers owned by the pipeline (Phase 7).

Two extra tables live next to the contract tables in app.db (created on demand):

  price_history(product_id, price, captured_at)
      A copy of each product's price whenever it changes. The contract `products` table only
      has TODAY's price, so without this we could never notice "the price of something you
      wishlisted went down" (price_drop trigger).

  pipeline_runs(run_id, user_id, trigger_type, status, run_at, dry_run)
      One row per pipeline run. The scheduler uses it as a cooldown so the same cart abandon
      isn't processed again every 15 minutes.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from common.db import get_connection, init_db

COOLDOWN_HOURS = 24  # same (user, trigger) is not re-run within this many hours


def ensure_tables(db_path: str | Path | None = None) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS price_history (product_id TEXT, price REAL, captured_at TEXT)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_price_history_product ON price_history (product_id)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pipeline_runs (run_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, "
            "trigger_type TEXT, status TEXT, run_at TEXT, dry_run INTEGER DEFAULT 0)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_runs_user ON pipeline_runs (user_id)")


# ---------------------------------------------------------------------------
# Price history
# ---------------------------------------------------------------------------


def snapshot_prices(now: datetime, db_path: str | Path | None = None) -> int:
    """Save the current price of every product whose price is new or changed. Returns rows added."""
    ensure_tables(db_path)
    stamp = now.isoformat(timespec="seconds")
    with get_connection(db_path) as conn:
        # Latest snapshot per product, compared with today's catalog price.
        changed = conn.execute(
            """
            SELECT p.product_id, p.price FROM products p
            LEFT JOIN (
                SELECT h.product_id, h.price FROM price_history h
                JOIN (SELECT product_id, MAX(captured_at) AS ts FROM price_history GROUP BY product_id) last
                  ON last.product_id = h.product_id AND last.ts = h.captured_at
            ) latest ON latest.product_id = p.product_id
            WHERE p.price IS NOT NULL AND (latest.price IS NULL OR ABS(latest.price - p.price) > 0.005)
            """
        ).fetchall()
        conn.executemany("INSERT INTO price_history VALUES (?, ?, ?)",
                         [(r["product_id"], r["price"], stamp) for r in changed])
    return len(changed)


def price_history(db_path: str | Path | None = None) -> dict[str, list[tuple[str, float]]]:
    """{product_id: [(captured_at, price), ...] oldest first}."""
    ensure_tables(db_path)
    out: dict[str, list[tuple[str, float]]] = {}
    with get_connection(db_path) as conn:
        for r in conn.execute("SELECT product_id, price, captured_at FROM price_history ORDER BY captured_at"):
            out.setdefault(r["product_id"], []).append((r["captured_at"], float(r["price"])))
    return out


# ---------------------------------------------------------------------------
# Pipeline runs (cooldown)
# ---------------------------------------------------------------------------


def record_run(user_id: str, trigger_type: str, status: str, now: datetime, dry_run: bool,
               db_path: str | Path | None = None) -> None:
    ensure_tables(db_path)
    with get_connection(db_path) as conn:
        conn.execute("INSERT INTO pipeline_runs (user_id, trigger_type, status, run_at, dry_run) VALUES (?, ?, ?, ?, ?)",
                     (user_id, trigger_type, status, now.isoformat(timespec="seconds"), int(dry_run)))


def recently_run(now: datetime, hours: int = COOLDOWN_HOURS, db_path: str | Path | None = None) -> set[tuple[str, str]]:
    """(user_id, trigger_type) pairs that already had a real (not dry) run in the last `hours`."""
    ensure_tables(db_path)
    since = (now - timedelta(hours=hours)).isoformat(timespec="seconds")
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT DISTINCT user_id, trigger_type FROM pipeline_runs WHERE dry_run = 0 AND run_at >= ?",
                            (since,)).fetchall()
    return {(r["user_id"], r["trigger_type"]) for r in rows}


def reachable_users(db_path: str | Path | None = None) -> set[str]:
    """Users we could message at all: opted in AND an active Telegram link."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT c.user_id FROM user_consent c JOIN telegram_links t ON t.user_id = c.user_id "
            "WHERE c.opted_in = 1 AND t.active = 1 AND t.chat_id IS NOT NULL"
        ).fetchall()
    return {r["user_id"] for r in rows}
