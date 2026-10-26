"""Shared fixtures for reco tests: ONE trained engine on a temp copy of the sample data.

Training takes a few seconds, so it happens once per test session (scope="session").
The temp DB also gets a brand-new product with zero interactions (cold-start item test).
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

NEW_PRODUCT = {
    "product_id": "P09999", "title": "boAt Rockerz 450 Wireless Headphones",
    "description": "boAt Rockerz 450 wireless bluetooth headphones. Popular choice in Mobile Accessories.",
    "category_id": "C010", "brand": "boAt", "price": 1499, "mrp": 2990, "margin_pct": 12.0, "avg_rating": None,
    "rating_count": 0, "stock_qty": 50, "launch_date": "2026-09-29", "tags": "mobile accessories|boat|headphones",
    "is_consumable": 0, "repurchase_days": None, "age_restricted": 0,
}


@pytest.fixture(scope="session")
def reco_db(tmp_path_factory):
    """Temp DB = sample data + one new product nobody has interacted with."""
    from common.db import get_connection, load_csvs_to_db

    db = tmp_path_factory.mktemp("reco") / "reco_test.db"
    load_csvs_to_db(ROOT / "data" / "sample", db_path=db)
    cols = ", ".join(NEW_PRODUCT)
    with get_connection(db) as conn:
        conn.execute(f"INSERT INTO products ({cols}) VALUES ({', '.join('?' * len(NEW_PRODUCT))})",
                     list(NEW_PRODUCT.values()))
    return db


@pytest.fixture(scope="session")
def engine(reco_db):
    """Fully trained engine (all candidate models + LightGBM ranker)."""
    from reco.train import train_serving_engine

    return train_serving_engine(reco_db)


@pytest.fixture
def service(engine, reco_db):
    """The public reco functions, pointed at the temp engine + DB."""
    import reco
    from reco import service as svc

    svc.use_engine(engine, reco_db)
    yield reco
    svc.use_engine(None)


@pytest.fixture
def add_event(reco_db):
    """Insert events into the temp DB; they are deleted again after the test."""
    from common.db import get_connection

    added = []

    def _add(user_id, product_id, event_type, timestamp="2026-09-30T23:00:00", session_id="S9999999"):
        event_id = f"E9{len(added):08d}"
        with get_connection(reco_db) as conn:
            conn.execute("INSERT INTO events (event_id, user_id, product_id, session_id, event_type, timestamp, "
                         "source) VALUES (?, ?, ?, ?, ?, ?, 'home')",
                         (event_id, user_id, product_id, session_id, event_type, timestamp))
        added.append(event_id)

    yield _add
    with get_connection(reco_db) as conn:
        conn.executemany("DELETE FROM events WHERE event_id = ?", [(e,) for e in added])
