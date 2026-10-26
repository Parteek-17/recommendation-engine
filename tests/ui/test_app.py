"""Smoke test: every storefront page renders without errors.

Streamlit's AppTest runs ui/app.py in-process (no browser). Instead of a real HTTP
server, the UI's requests are routed straight into the FastAPI app via TestClient.
"""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from fastapi.testclient import TestClient  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from common import stubs  # noqa: E402
from common.config import settings  # noqa: E402

APP = str(Path(__file__).resolve().parents[2] / "ui" / "app.py")
PAGES = ["🏠 Home", "📦 Product", "🛒 Cart", "🎛️ My controls", "💬 Chat", "📊 Admin / Insights"]


class _Bridge:
    """Looks like the `requests` module, but sends calls into the FastAPI app."""

    ConnectionError = ConnectionError

    def __init__(self, client):
        self.client = client

    @staticmethod
    def _path(url):
        return url.replace(settings.MAIN_API_URL.rstrip("/"), "")

    def get(self, url, params=None, timeout=None):
        return self.client.get(self._path(url), params=params)

    def post(self, url, json=None, timeout=None):
        return self.client.post(self._path(url), json=json)

    def put(self, url, json=None, timeout=None):
        return self.client.put(self._path(url), json=json)


@pytest.fixture
def app_test(sample_db, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", sample_db)
    stubs._table.cache_clear()
    from api import personalize
    from api.main import app
    from ui import api_client

    personalize.clear_cache()
    monkeypatch.setattr(api_client, "requests", _Bridge(TestClient(app)))
    yield AppTest.from_file(APP, default_timeout=120)
    stubs._table.cache_clear()


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(app_test, page):
    app_test.session_state["page"] = page
    app_test.session_state["product_id"] = "P00001"
    app_test.run()
    assert not app_test.exception, app_test.exception


def test_home_cards_show_reasons(app_test):
    app_test.run()
    reasons = [box.value for box in app_test.info]
    assert len(reasons) >= 5 and all(reasons)
