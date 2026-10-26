"""Smoke test: every Control Room page renders without errors (Streamlit AppTest, no browser)."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[2] / "ui" / "control_room.py")
PAGES = ["📏 Offer rules", "📈 Results", "🧪 Demo testers"]


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page):
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception, at.exception


def test_rules_example_reacts_to_sliders():
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    before = at.metric[0].value
    at.slider[0].set_value(5).run()  # biggest discount allowed -> 5%
    assert not at.exception
    assert at.metric[0].value.startswith("5%") and at.metric[0].value != before
