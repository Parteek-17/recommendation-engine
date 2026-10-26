"""offers/rules.yaml: the marketing team's file. Valid edits apply live; mistakes never switch limits off."""

from __future__ import annotations

import shutil

import pytest

from common.config import settings
from common.schemas import TriggerType
from offers import rules


@pytest.fixture
def rules_copy(tmp_path):
    """A private copy of rules.yaml; the real rules are restored afterwards."""
    path = tmp_path / "rules.yaml"
    shutil.copy(rules.RULES_FILE, path)
    yield path
    rules.refresh_rules(force=True)


def test_shipped_rules_file_is_valid():
    assert rules.validate_rules(rules.load_rules()) == []


def test_save_applies_new_limits_and_keeps_comments(rules_copy):
    data = rules.load_rules(rules_copy)
    data["limits"]["max_discount_pct"] = 25
    data["starting_discount_pct"]["win_back"] = 12
    data["skip_likely_buyers"]["triggers"] = ["cart_abandon"]
    assert rules.save_rules(data, rules_copy) == []
    assert settings.MAX_DISCOUNT_PCT == 25
    assert rules.BASE_DISCOUNT_PCT[TriggerType.WIN_BACK] == 12
    assert rules.SKIP_LIKELY_BUYERS["triggers"] == ["cart_abandon"]
    text = rules_copy.read_text(encoding="utf-8")
    assert "max_discount_pct: 25" in text and "# No offer is ever bigger than this" in text
    assert rules.load_rules(rules_copy) == data


def test_invalid_values_are_refused_and_nothing_changes(rules_copy):
    before = rules_copy.read_text(encoding="utf-8")
    data = rules.load_rules(rules_copy)
    data["limits"]["max_discount_pct"] = 95
    data["frequency"]["quiet_start"] = "late"
    problems = rules.save_rules(data, rules_copy)
    assert any("max_discount_pct" in p for p in problems) and any("quiet_start" in p for p in problems)
    assert rules_copy.read_text(encoding="utf-8") == before


def test_broken_file_keeps_last_good_rules(rules_copy):
    rules.refresh_rules(force=True, path=rules_copy)
    cap = settings.MAX_DISCOUNT_PCT
    rules_copy.write_text("limits: [this is: not valid", encoding="utf-8")
    assert rules.refresh_rules(force=True, path=rules_copy) is False
    assert rules.last_error and settings.MAX_DISCOUNT_PCT == cap
