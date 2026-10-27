"""Smoke test: every Admin dashboard page renders without errors (Streamlit AppTest, no browser)."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[2] / "ui" / "control_room.py")
PAGES = ["🏠 Overview", "🎁 Create offer", "📋 Offers", "🤖 Assistant", "📏 Offer rules", "📈 Results",
         "🧪 Demo testers"]


def open_page(page: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    at.sidebar.radio[0].set_value(page).run()
    return at


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page):
    at = open_page(page)
    assert not at.exception, at.exception


def test_rules_example_reacts_to_sliders():
    at = open_page("📏 Offer rules")
    before = at.metric[0].value
    at.slider[0].set_value(5).run()  # biggest discount allowed -> 5%
    assert not at.exception
    assert at.metric[0].value.startswith("5%") and at.metric[0].value != before


def test_create_offer_preview_follows_the_rules():
    at = open_page("🎁 Create offer")
    assert any("Passes every offer rule" in s.value for s in at.success)
    at.slider[0].set_value(85).run()  # discount far above the cap
    assert not at.exception
    assert any("Not allowed" in e.value for e in at.error)
    create = next(b for b in at.button if "Create offer" in b.label)
    assert create.disabled
