"""Settings load with defaults, secrets are masked, missing keys give friendly errors."""

import pytest

from common.config import ConfigError, Settings, mask


def test_mask_hides_secret():
    assert mask("sk-ant-abcdefghijklmnopxyz") == "sk-...xyz"
    assert mask("") == "(empty)"
    assert mask("short") == "***"


def test_require_gives_friendly_error():
    s = Settings()
    with pytest.raises(ConfigError, match="SETUP_GUIDE"):
        s.require("ANTHROPIC_API_KEY")
    with pytest.raises(ConfigError):
        Settings(OFFERS_API_KEY="change-me").require("OFFERS_API_KEY")


def test_repr_never_leaks_keys():
    s = Settings(ANTHROPIC_API_KEY="sk-ant-supersecretvalue123")
    assert "supersecret" not in repr(s)
    assert s.describe()["ANTHROPIC_API_KEY"] == "sk-...123"


def test_defaults():
    s = Settings()
    assert s.MAX_DISCOUNT_PCT == 40 and s.DATA_SOURCE == "sample" and s.TIMEZONE == "Asia/Kolkata"
