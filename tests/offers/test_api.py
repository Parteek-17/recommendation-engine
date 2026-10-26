"""Offers API: key protection (401), routes, docs."""

import pytest
from fastapi.testclient import TestClient

from common.config import settings
from offers.api import app

KEY = "test-key-not-a-real-secret"


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(settings, "OFFERS_API_KEY", KEY)
    return TestClient(app)


def auth(key=KEY):
    return {"X-API-Key": key}


def test_health_needs_no_key(client):
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/offers/live", "/offers/OF0001"])
def test_missing_or_wrong_key_gives_401(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers=auth("wrong")).status_code == 401
    assert client.get(path, headers=auth(KEY.upper())).status_code == 401


def test_generate_needs_key(client):
    assert client.post("/offers/generate", json={"trigger_type": "festival"}).status_code == 401


def test_placeholder_key_on_server_refuses_everything(client, monkeypatch):
    monkeypatch.setattr(settings, "OFFERS_API_KEY", "change-me")
    assert client.get("/offers/live", headers=auth("change-me")).status_code == 503


def test_live_offers_with_key(client, monkeypatch):
    # Pin "now" inside the sample offers' window so the test doesn't depend on today's date.
    from offers import segments
    from .conftest import NOW
    monkeypatch.setattr(segments, "now_local", lambda: NOW)

    resp = client.get("/offers/live", headers=auth())
    assert resp.status_code == 200
    ids = {o["offer_id"] for o in resp.json()}
    assert "OF0001" in ids
    assert "OF0018" not in ids  # expired
    assert "OF0019" not in ids  # inactive


def test_live_offers_filtered_by_user_segment_and_trigger(client, monkeypatch):
    from offers import segments
    from .conftest import NOW
    monkeypatch.setattr(segments, "now_local", lambda: NOW)

    tier3 = {o["offer_id"] for o in client.get("/offers/live?user_id=U00001", headers=auth()).json()}
    tier1 = {o["offer_id"] for o in client.get("/offers/live?user_id=U00003", headers=auth()).json()}
    assert "OF0004" in tier3 and "OF0004" not in tier1  # FREESHIP is for tier-3 cities only

    carts = client.get("/offers/live?trigger_type=cart_abandon", headers=auth()).json()
    assert carts and all(o["trigger_type"] == "cart_abandon" for o in carts)
    assert client.get("/offers/live?trigger_type=nonsense", headers=auth()).status_code == 422


def test_get_offer_and_404(client):
    resp = client.get("/offers/OF0005", headers=auth())
    assert resp.status_code == 200 and resp.json()["code"] == "PHONECOVER"
    assert client.get("/offers/OF9999", headers=auth()).status_code == 404


def test_generate(client):
    resp = client.post("/offers/generate", headers=auth(), json={"trigger_type": "cart_abandon", "user_id": "U00004"})
    assert resp.status_code == 200
    created = resp.json()
    assert created and all(o["segment"] == "user:U00004" for o in created)
    assert client.get(f"/offers/{created[0]['offer_id']}", headers=auth()).status_code == 200


def test_docs_page_works(client):
    assert client.get("/docs").status_code == 200
    assert "/offers/live" in client.get("/openapi.json").json()["paths"]
