"""End-to-end checks on a generated dataset: contract, validation, seeds, ground truth, DB load."""

import pandas as pd

from common.db import TABLES, columns, get_data_level, load_csvs_to_db
from data_gen import generate as gen
from data_gen.validate import validate_folder


def test_every_contract_table_is_written_with_exact_columns(tiny_folders):
    raw, _ = tiny_folders
    for table in TABLES:
        df = pd.read_csv(raw / f"{table}.csv", dtype=str)
        assert list(df.columns) == columns(table), table


def test_validation_passes_strict(tiny_folders):
    raw, gt = tiny_folders
    report = validate_folder(raw, ground_truth=gt, strict=True)
    assert report.ok, report.errors
    assert report.level == "L4"
    assert all(report.features.values())


def test_validator_catches_problems(tiny_folders, tmp_path):
    raw, _ = tiny_folders
    for table in TABLES:
        (tmp_path / f"{table}.csv").write_bytes((raw / f"{table}.csv").read_bytes())
    ev = pd.read_csv(tmp_path / "events.csv", dtype=str, keep_default_na=False)
    ev.loc[0, "event_type"] = "teleport"          # bad enum
    ev.loc[1, "user_id"] = "U99999"               # broken foreign key
    ev.to_csv(tmp_path / "events.csv", index=False)
    (tmp_path / "product_relations.csv").write_text("product_id,related_product_id,relation\n")
    report = validate_folder(tmp_path, strict=True)
    text = " ".join(report.errors)
    assert not report.ok
    assert "event_type" in text and "events.user_id -> users.user_id" in text
    assert "inside" in text   # ground truth in the raw folder is an error


def test_same_seed_gives_identical_data(tiny):
    again, truth_again = gen.generate("tiny", verbose=False)
    tables, truth = tiny
    for name in ("products", "users", "events", "orders", "reviews", "offer_sends", "user_consent"):
        pd.testing.assert_frame_equal(tables[name].reset_index(drop=True), again[name].reset_index(drop=True))
    pd.testing.assert_frame_equal(truth["product_relations"], truth_again["product_relations"])


def test_different_seed_gives_different_data(tiny):
    other, _ = gen.generate("tiny", seed=7, verbose=False)
    assert not tiny[0]["events"]["product_id"].head(200).equals(other["events"]["product_id"].head(200))


def test_ground_truth_kept_out_of_raw(tiny_folders, tmp_path):
    raw, gt = tiny_folders
    assert not (raw / "user_hidden_profile.csv").exists()
    assert not (raw / "product_relations.csv").exists()
    assert (gt / "user_hidden_profile.csv").exists() and (gt / "product_relations.csv").exists()
    # The CLI refuses to put ground truth inside the raw folder.
    assert gen.main(["--size", "tiny", "--out", str(tmp_path), "--ground-truth", str(tmp_path / "gt"),
                     "--no-db"]) == 2


def test_loads_into_db_as_l4(tiny_folders, tmp_db):
    raw, _ = tiny_folders
    loaded = load_csvs_to_db(raw, db_path=tmp_db)
    assert loaded["events"] > 1000
    assert get_data_level(tmp_db) == "L4"
