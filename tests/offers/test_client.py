"""Client: retries with backoff, returns [] (never fake offers) when the API is down, logs to audit_log."""

import socket

import pytest
import requests

from common.config import settings
from offers import client as client_module
from offers.client import fetch_live_offers

from .conftest import audit_rows

KEY = "test-key-not-a-real-secret"


@pytest.fixture(autouse=True)
def setup(db, monkeypatch):
    monkeypatch.setattr(settings, "OFFERS_API_KEY", KEY)
    sleeps = []
    monkeypatch.setattr(client_module, "_sleep", sleeps.append)  # don't really wait
    return sleeps


class FakeResponse:
    def __init__(self, status_code, data=None):
        self.status_code, self._data = status_code, data

    def json(self):
        return self._data


SAMPLE_OFFER = {"offer_id": "OF0001", "code": "CART10", "offer_type": "pct_off", "value": 10,
                "start_at": "2026-09-01T00:00:00", "expires_at": "2027-03-31T23:59:59"}


def test_success_sends_key_in_header_and_parses_offers(monkeypatch):
    seen = {}

    def fake_get(url, params, headers, timeout):
        seen.update(url=url, params=params, headers=headers, timeout=timeout)
        return FakeResponse(200, [SAMPLE_OFFER])

    monkeypatch.setattr(requests, "get", fake_get)
    offers = fetch_live_offers("U00001")
    assert [o.offer_id for o in offers] == ["OF0001"]
    assert seen["url"].endswith("/offers/live") and seen["params"] == {"user_id": "U00001"}
    assert seen["headers"] == {"X-API-Key": KEY}
    assert seen["timeout"]  # always a timeout


def test_api_down_returns_empty_list_after_retries_and_audits(db, monkeypatch, setup):
    calls = []

    def down(*args, **kwargs):
        calls.append(1)
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", down)
    assert fetch_live_offers("U00001", db_path=db) == []
    assert len(calls) == client_module.RETRIES
    assert setup == [0.5, 1.0]  # exponential backoff between attempts
    (row,) = audit_rows(db, "fetch_live_offers")
    assert row["decision"] == "failed" and "ConnectionError" in row["reason"]
    assert KEY not in row["details_json"] and KEY not in row["reason"]  # key never logged


def test_real_closed_port_returns_empty_list(db, monkeypatch):
    """No mocking: point the client at a port where nothing is listening."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # free port; closed again when the block ends
    monkeypatch.setattr(settings, "OFFERS_API_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(client_module, "RETRIES", 1)
    assert fetch_live_offers(db_path=db) == []
    assert audit_rows(db, "fetch_live_offers")


def test_server_error_is_retried_then_gives_up(db, monkeypatch):
    responses = iter([FakeResponse(500), FakeResponse(503), FakeResponse(200, [SAMPLE_OFFER])])
    monkeypatch.setattr(requests, "get", lambda *a, **k: next(responses))
    assert [o.offer_id for o in fetch_live_offers(db_path=db)] == ["OF0001"]  # third try works


def test_wrong_key_401_is_not_retried(db, monkeypatch):
    calls = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(1) or FakeResponse(401))
    assert fetch_live_offers(db_path=db) == []
    assert len(calls) == 1
    assert "HTTP 401" in audit_rows(db, "fetch_live_offers")[0]["reason"]


def test_bad_json_returns_empty_list(db, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(200, [{"offer_id": "broken"}]))
    assert fetch_live_offers(db_path=db) == []


def test_missing_key_config_returns_empty_list_without_calling_api(db, monkeypatch):
    monkeypatch.setattr(settings, "OFFERS_API_KEY", "")
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("must not call the API without a key"))
    assert fetch_live_offers(db_path=db) == []
    assert "config error" in audit_rows(db, "fetch_live_offers")[0]["reason"]


def test_read_timeout_is_not_retried(db, monkeypatch):
    """The server got the request but is slow: asking again would only queue the same work twice."""
    calls = []

    def slow(*args, **kwargs):
        calls.append(1)
        raise requests.ReadTimeout("read timed out")

    monkeypatch.setattr(requests, "get", slow)
    assert fetch_live_offers("U00001", db_path=db) == []
    assert len(calls) == 1
    (row,) = audit_rows(db, "fetch_live_offers")
    assert "ReadTimeout" in row["reason"]
