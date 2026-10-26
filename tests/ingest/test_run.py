"""Each mapping on its tiny hand-made fixture: converts, validates, loads, reports the right level."""

from pathlib import Path

import pandas as pd
import pytest

from common.db import get_data_level, read_table
from ingest.run import load_mapping, run

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "ingest" / "fixtures"
MAPS = ROOT / "ingest" / "mappings"


def csv(folder: Path, table: str) -> pd.DataFrame:
    return pd.read_csv(folder / f"{table}.csv", dtype=str, keep_default_na=False)


def test_retailrocket_is_l1_and_loads(tmp_path, tmp_db):
    res = run(MAPS / "retailrocket.yaml", input_dir=FIX / "retailrocket", out_dir=tmp_path / "rr", db_path=tmp_db)
    assert res.report.ok, res.report.errors
    assert res.report.level == "L1"
    assert get_data_level(tmp_db) == "L1"
    ev = csv(res.out_dir, "events")
    assert len(ev) == 11 and len(read_table("events", tmp_db)) == 11
    assert set(ev["event_type"]) == {"view", "add_to_cart", "purchase"}
    assert ev["timestamp"].iloc[0] == "2025-09-01T13:30:00"       # unix ms -> IST
    drops = res.tracker.drops["events"]
    for reason in ("unknown event type", "missing user_id", "missing or unreadable timestamp",
                   "missing product_id", "duplicate (same user, product, type, time)"):
        assert drops[reason] == 1, reason
    # Sessions were built with the 30-minute rule: user 992329 has a 2-hour gap -> 2 sessions.
    u = csv(res.out_dir, "id_map").query("entity == 'user' and external_id == '992329'")["internal_id"].iloc[0]
    assert ev.loc[ev["user_id"] == u, "session_id"].nunique() == 2
    assert len(csv(res.out_dir, "orders")) == 2                    # 1 per user per day with a purchase
    report = (res.out_dir / "report.md").read_text(encoding="utf-8")
    assert "Data level: L1" in report and "CC BY-NC-SA" in report


def test_amazon_reviews_catalog(tmp_path, tmp_db):
    res = run(MAPS / "amazon_reviews_2023.yaml", input_dir=FIX / "amazon", out_dir=tmp_path / "amz", db_path=tmp_db)
    assert res.report.ok, res.report.errors
    assert res.report.level == "L3"
    p = csv(res.out_dir, "products").set_index("title")
    assert float(p.loc["Galaxy Phone 128GB", "price"]) == round(199.99 * 84, 2)     # USD -> ₹
    assert float(p.loc["Wireless Earbuds", "price"]) == round(24.50 * 84, 2)         # "$24.50" string
    assert p.loc["Galaxy Phone 128GB", "description"] == "A great phone."              # HTML stripped
    assert p.loc["Galaxy Phone 128GB", "tags"] == "6.5 inch display|5000mAh fast charge"
    assert "Unused Cable" not in p.index                                                # never reviewed
    assert any(t.startswith("Product P") for t in p.index)                              # review without meta
    cats = csv(res.out_dir, "categories")
    assert {"Electronics", "Cell Phones", "Unknown"} <= set(cats["name"])
    reviews = csv(res.out_dir, "reviews")
    assert len(reviews) == 5 and reviews["review_text"].iloc[0] == "Works perfectly! Fast charging."
    assert res.tracker.drops["reviews"]["missing user, product or rating"] == 1
    # Every review line is a purchase event (also the one without a rating); events + reviews
    # describe the same purchases, so nothing is counted twice.
    ev = csv(res.out_dir, "events")
    assert len(ev) == 6 and (ev["event_type"] == "purchase").all()


def test_myntra_catalog_only_does_not_crash(tmp_path, tmp_db):
    res = run(MAPS / "myntra_fashion.yaml", input_dir=FIX / "myntra", out_dir=tmp_path / "my", db_path=tmp_db)
    assert res.report.ok, res.report.errors
    assert res.report.level == "L0"                    # no events at all
    p = csv(res.out_dir, "products").set_index("title")
    assert float(p.loc["Roadster Men Navy Blue Slim Fit T-shirt", "price"]) == 449.0      # "₹449"
    assert float(p.loc["Puma Women Running Shoes", "price"]) == 2799.0                    # "Rs. 2,799"
    assert p.loc["Plain Socks", "category_id"] == "UNKNOWN" and p.loc["Plain Socks", "brand"] == "Unknown"
    assert float(p.loc["Libas Women Pink Kurta Set", "margin_pct"]) == 40.0                # Fashion default
    drops = res.tracker.drops["products"]
    assert drops["missing product_id"] == 1 and drops["duplicate product_id"] == 1
    # Personal data never reaches the output.
    for f in res.out_dir.glob("*.csv"):
        assert "@example.com" not in f.read_text(encoding="utf-8")
    assert "seller_email" in (res.out_dir / "report.md").read_text(encoding="utf-8")


def test_myntra_fill_missing_simulates_shoppers(tmp_path, tmp_db):
    res = run(MAPS / "myntra_fashion.yaml", input_dir=FIX / "myntra", out_dir=tmp_path / "myf", db_path=tmp_db,
              fill_missing=True)
    assert res.report.ok, res.report.errors
    assert res.report.level == "L4"
    assert get_data_level(tmp_db) == "L4"
    ev = csv(res.out_dir, "events")
    assert set(ev["product_id"]) - {""} <= set(csv(res.out_dir, "products")["product_id"])   # "" = search
    assert (res.out_dir / "ground_truth" / "user_hidden_profile.csv").exists()


def test_l1_only_minimal_columns(tmp_path, tmp_db):
    """Just user, item and a date (no event type, no catalog): defaults kick in, nothing crashes."""
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "log.csv").write_text("who,what,when\nalice@example.com,sku1,12-08-2026 10:00\n"
                                 "alice@example.com,sku2,12-08-2026 10:05\nbob,sku1,13-08-2026 21:00\n",
                                 encoding="utf-8")
    mapping = tmp_path / "mini.yaml"
    mapping.write_text("source: mini\nfiles:\n  events:\n    path: data/external/mini/log.csv\n    format: csv\n"
                       "    map: {user_id: who, product_id: what, timestamp: when}\n", encoding="utf-8")
    res = run(mapping, input_dir=raw, out_dir=tmp_path / "out", db_path=tmp_db, fill_missing=True)
    assert res.report.ok, res.report.errors
    ev = csv(res.out_dir, "events")
    assert (ev["event_type"] == "view").all() and (ev["source"] == "home").all()
    assert ev["timestamp"].iloc[0] == "2026-08-12T10:00:00"                     # day-first date
    id_map = csv(res.out_dir, "id_map")
    assert not id_map["external_id"].str.contains("@").any()                      # emails hashed
    assert id_map["external_id"].str.startswith("sha256:").any()
    users = csv(res.out_dir, "users")
    assert (users["age_group"] != "unknown").all()                                # --fill-missing
    assert len(csv(res.out_dir, "offers")) > 0 and len(csv(res.out_dir, "user_consent")) == 2
    assert res.report.level == "L1"                                               # still no catalog


def test_sample_keeps_n_users(tmp_path, tmp_db):
    res = run(MAPS / "retailrocket.yaml", input_dir=FIX / "retailrocket", out_dir=tmp_path / "s", db_path=tmp_db,
              sample=1)
    assert csv(res.out_dir, "events")["user_id"].nunique() == 1


def test_synthetic_mapping_passes_same_validator(tmp_path, tmp_db):
    from data_gen.generate import generate
    from data_gen.utils import write_tables

    tables, _ = generate("tiny", verbose=False)
    write_tables(tables, tmp_path / "raw")
    res = run(MAPS / "synthetic.yaml", input_dir=tmp_path / "raw", out_dir=tmp_path / "syn", db_path=tmp_db)
    assert res.report.ok, res.report.errors
    assert res.report.level == "L4"
    assert len(csv(res.out_dir, "events")) == len(tables["events"])


@pytest.mark.parametrize("name", ["_template", "amazon_reviews_2023", "retailrocket", "myntra_fashion", "synthetic"])
def test_every_mapping_file_parses(name):
    cfg = load_mapping(MAPS / f"{name}.yaml")
    assert cfg["source"] and cfg["files"]


def test_bad_mapping_field_is_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("source: x\nfiles:\n  events:\n    path: a.csv\n    map: {usr: who}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown fields"):
        load_mapping(bad)
