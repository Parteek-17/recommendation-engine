"""Fixtures for offers tests: a temp DB with the sample data, a fixed clock, small DB helpers."""

import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from common.db import get_connection

# A fixed "now" inside every sample offer's validity window, at midday (not quiet hours).
NOW = datetime(2026, 10, 1, 12, 0, 0)


@pytest.fixture
def now():
    return NOW


@pytest.fixture(scope="session")
def _sample_template(tmp_path_factory):
    """Load the sample CSVs ONCE per test run (slow); each test then gets a cheap file copy."""
    from common.db import load_csvs_to_db

    path = tmp_path_factory.mktemp("offers_template") / "template.db"
    load_csvs_to_db(Path(__file__).resolve().parents[2] / "data" / "sample", db_path=path)
    return path


@pytest.fixture
def db(_sample_template, tmp_path, monkeypatch):
    """Sample-data DB in a temp folder. Also made the default DB, so nothing touches data/app.db."""
    from common.config import settings

    path = tmp_path / "test.db"
    shutil.copy(_sample_template, path)
    monkeypatch.setattr(settings, "DB_PATH", path)
    return path


def execute(db_path, sql, params=()):
    with get_connection(db_path) as conn:
        conn.execute(sql, params)


def add_offer(db_path, offer_id, **fields):
    """Insert an offer; anything not given gets a sensible, valid default."""
    row = {"offer_id": offer_id, "code": f"TEST-{offer_id}", "offer_type": "pct_off", "value": 10,
           "min_cart_value": 0, "category_id": None, "product_id": None, "bundle_product_id": None,
           "segment": "all", "trigger_type": "festival", "max_uses_per_user": 1,
           "start_at": "2026-09-01T00:00:00", "expires_at": "2027-03-31T23:59:59",
           "is_active": 1, "requires_approval": 0, **fields}
    cols = ", ".join(row)
    with get_connection(db_path) as conn:
        conn.execute(f"INSERT INTO offers ({cols}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))


def add_send(db_path, send_id, user_id, offer_id, status="sent", sent_at="2026-10-01T09:00:00"):
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO offer_sends VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (send_id, user_id, offer_id, "festival", "test", "telegram", status, sent_at, sent_at),
        )


def audit_rows(db_path, action=None):
    """audit_log rows as dicts (details_json parsed)."""
    sql, params = "SELECT * FROM audit_log", ()
    if action:
        sql, params = sql + " WHERE action = ?", (action,)
    with get_connection(db_path) as conn:
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY id", params)]
    for r in rows:
        r["details"] = json.loads(r["details_json"] or "{}")
    return rows
