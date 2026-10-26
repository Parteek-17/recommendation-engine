"""Shared pytest setup: make the project root importable and give tests a throwaway DB."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def tmp_db(tmp_path):
    """Path to a fresh, empty SQLite file inside pytest's temp folder (never touches data/app.db)."""
    return tmp_path / "test.db"


@pytest.fixture
def sample_db(tmp_db):
    """A temp DB with all tables created and the sample CSVs loaded."""
    from common.db import load_csvs_to_db

    load_csvs_to_db(ROOT / "data" / "sample", db_path=tmp_db)
    return tmp_db


@pytest.fixture(scope="session")
def sample_engine(tmp_path_factory):
    """Real reco engine trained ONCE per test run on the sample data (~6 s).

    Tests that use the real `reco` on a temp sample DB need this: otherwise reco loads
    models/reco_bundle.pkl, which was trained on DATA_SOURCE (e.g. synthetic) where the same
    product IDs mean different products.
    """
    from common.db import load_csvs_to_db
    from reco.train import train_serving_engine

    db = tmp_path_factory.mktemp("sample_engine") / "engine.db"
    load_csvs_to_db(ROOT / "data" / "sample", db_path=db)
    return train_serving_engine(db)


@pytest.fixture
def use_sample_engine(sample_engine):
    """Point the real reco at the sample-trained engine for one test (it reads settings.DB_PATH)."""
    from reco import service

    service.use_engine(sample_engine)
    yield sample_engine
    service.use_engine(None)


@pytest.fixture(scope="session")
def _sample_db_template(tmp_path_factory):
    """Sample CSVs loaded ONCE; each test gets a cheap file copy (see _hermetic_defaults)."""
    from common.db import load_csvs_to_db

    path = tmp_path_factory.mktemp("sample_template") / "sample.db"
    load_csvs_to_db(ROOT / "data" / "sample", db_path=path)
    return path


@pytest.fixture(autouse=True)
def _hermetic_defaults(_sample_db_template, sample_engine, tmp_path, monkeypatch):
    """Every test starts on the SAMPLE data, never on data/app.db (which may hold the full
    synthetic data after Phase 7) and never on models/reco_bundle.pkl.

    - default DB (settings.DB_PATH) = a fresh copy of the sample DB; fixtures like `sample_db`
      or `db` may point it somewhere else again, that's fine
    - DATA_SOURCE = sample, so data_folder() / ground truth are the sample ones
    - the real reco uses the sample-trained engine, and can never overwrite the saved model
    """
    import shutil

    from common import stubs
    from common.config import settings
    from reco import service

    db = tmp_path / "default.db"
    shutil.copy(_sample_db_template, db)
    monkeypatch.setattr(settings, "DB_PATH", db)
    monkeypatch.setattr(settings, "DATA_SOURCE", "sample")
    tmp_model, real_save = tmp_path / "reco_bundle.pkl", service.save_engine
    monkeypatch.setattr(service, "MODEL_PATH", tmp_model)
    # save_engine's default path was fixed when reco was imported, so redirect it explicitly.
    monkeypatch.setattr(service, "save_engine", lambda engine, path=tmp_model: real_save(engine, path))
    stubs._table.cache_clear()
    service.use_engine(sample_engine)
    yield
    service.use_engine(None)
    stubs._table.cache_clear()
