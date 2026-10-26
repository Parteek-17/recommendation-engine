"""init_db creates every contract table; sample CSVs load; data level + features work."""

import json

import pytest

from common.db import (
    TABLES,
    columns,
    feature_enabled,
    get_connection,
    get_data_level,
    init_db,
    read_ground_truth,
    read_table,
    table_counts,
)
from common.logging_utils import audit


def test_init_db_creates_all_tables(tmp_db):
    init_db(tmp_db)
    counts = table_counts(tmp_db)
    assert set(counts) == set(TABLES)
    assert all(n == 0 for n in counts.values())  # created, still empty
    with get_connection(tmp_db) as conn:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(products)")]
    assert cols == columns("products")


def test_init_db_is_safe_to_run_twice(tmp_db):
    init_db(tmp_db)
    init_db(tmp_db)


def test_empty_db_is_level_l0(tmp_db):
    init_db(tmp_db)
    assert get_data_level(tmp_db) == "L0"
    assert feature_enabled("popularity", tmp_db) is False


def test_sample_data_loads_every_table(sample_db):
    counts = table_counts(sample_db)
    for table in ("users", "products", "categories", "events", "orders", "offers", "user_consent"):
        assert counts[table] > 0, table
    assert counts["users"] == 50 and counts["products"] == 100 and counts["offers"] == 20
    assert 1500 <= counts["events"] <= 2600


def test_sample_data_is_level_l4(sample_db):
    assert get_data_level(sample_db) == "L4"
    assert feature_enabled("offers", sample_db)
    assert feature_enabled("content", sample_db)


def test_feature_enabled_rejects_typos(sample_db):
    with pytest.raises(ValueError):
        feature_enabled("contnet", sample_db)


def test_read_table_returns_dataframe(sample_db):
    products = read_table("products", sample_db)
    assert list(products.columns) == columns("products")
    assert products["price"].dtype.kind == "f"  # numbers came back as numbers
    with pytest.raises(ValueError):
        read_table("products; DROP TABLE users", sample_db)


def test_planted_cases_exist(sample_db):
    products = read_table("products", sample_db).set_index("product_id")
    assert products.loc["P00004", "is_consumable"] == 1 and products.loc["P00004", "repurchase_days"] == 30
    assert products["age_restricted"].sum() >= 1
    users = read_table("users", sample_db)
    assert users["is_minor"].sum() >= 1
    sessions = read_table("sessions", sample_db)
    assert sessions["is_gift"].sum() >= 1
    events = read_table("events", sample_db)
    assert "U00050" not in set(events["user_id"])  # brand-new user, no history
    relations = read_ground_truth("product_relations", "sample")
    pairs = set(zip(relations["product_id"], relations["related_product_id"], relations["relation"]))
    assert ("P00001", "P00002", "complement") in pairs
    assert ("P00001", "P00003", "substitute") in pairs


def test_audit_writes_a_row(tmp_db):
    audit("offers", "U00001", "send_offer", "blocked", "frequency cap", {"offer_id": "OF0001"}, db_path=tmp_db)
    rows = read_table("audit_log", tmp_db)
    assert len(rows) == 1
    assert rows.iloc[0]["decision"] == "blocked"
    assert json.loads(rows.iloc[0]["details_json"]) == {"offer_id": "OF0001"}
