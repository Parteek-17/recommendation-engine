"""Shared setup for LLM tests: always mock mode, sample data, no audit rows in the real DB."""

import pandas as pd
import pytest

from common import stubs
from common.db import data_folder
from llm import agent, catalog, guardrails, offer_writer, tools
from llm.client import LLMClient, set_client


def _sample_table(name: str) -> pd.DataFrame:
    path = data_folder("sample") / f"{name}.csv"
    return pd.read_csv(path, keep_default_na=False) if path.exists() else pd.DataFrame()


@pytest.fixture(autouse=True)
def llm_test_env(monkeypatch):
    # Mock client: tests never call the real Claude API, even if a key is in .env.
    set_client(LLMClient(api_key=""))
    # Catalog facts from the fixed sample CSVs (not whatever is loaded in app.db).
    monkeypatch.setattr(catalog, "_table", _sample_table)
    catalog._products_by_id.cache_clear()
    guardrails._catalog_words.cache_clear()
    # Offers from the stub (sample offers.csv) so tests don't need the Offers API running.
    monkeypatch.setattr(tools, "fetch_live_offers", stubs.fetch_live_offers)
    # Don't write test traffic into data/app.db's audit_log.
    monkeypatch.setattr(agent, "audit", lambda *a, **k: None)
    monkeypatch.setattr(offer_writer, "audit", lambda *a, **k: None)
    guardrails.reset_rate_limits()
    agent.reset_history()
    yield
    set_client(None)
    catalog._products_by_id.cache_clear()
    guardrails._catalog_words.cache_clear()
