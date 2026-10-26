"""Project settings, loaded once from the `.env` file.

Usage (from any module):

    from common.config import settings
    print(settings.DB_PATH)                       # a pathlib.Path
    key = settings.require("ANTHROPIC_API_KEY")   # friendly error if empty

Secrets are optional on purpose: e.g. without ANTHROPIC_API_KEY the LLM module
runs in mock mode. Only call `settings.require(...)` where a key is truly needed.
Never print a secret — use `mask(value)` or `settings.describe()` instead.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path

from dotenv import load_dotenv

# The project root is the folder that contains common/ (one level up from this file).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
SETUP_HELP = "See docs/SETUP_GUIDE.md"

# Names of settings whose values must never be shown in full.
SECRET_KEYS = ("ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "OFFERS_API_KEY")


class ConfigError(RuntimeError):
    """Raised when a setting is missing or invalid. The message tells the user how to fix it."""


def mask(value: str | None) -> str:
    """Hide a secret, keeping only a hint: 'sk-ant-abc123xyz' -> 'sk-...xyz'."""
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}...{value[-3:]}"


def _get_int(name: str, default: int) -> int:
    """Read an integer setting; a typo like MAX_OFFERS_PER_DAY=one gives a clear message."""
    raw = os.getenv(name, "").strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got '{raw}'. Fix it in .env. {SETUP_HELP}") from None


def _get_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass
class Settings:
    """All settings in one place. Defaults match .env.example."""

    # Secrets
    ANTHROPIC_API_KEY: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    OFFERS_API_KEY: str = ""
    # Claude models
    CLAUDE_MODEL_FAST: str = ""
    CLAUDE_MODEL_SMART: str = ""
    # Telegram
    TELEGRAM_BOT_USERNAME: str = ""
    # General
    TIMEZONE: str = "Asia/Kolkata"
    DB_PATH: Path = PROJECT_ROOT / "data" / "app.db"
    DATA_SOURCE: str = "sample"
    EXTERNAL_SOURCE: str = ""
    OFFERS_API_URL: str = "http://127.0.0.1:8001"
    MAIN_API_URL: str = "http://127.0.0.1:8000"
    # Guardrail limits
    MAX_DISCOUNT_PCT: int = 40
    MAX_OFFERS_PER_DAY: int = 1
    MAX_OFFERS_PER_WEEK: int = 3
    QUIET_START: str = "22:00"
    QUIET_END: str = "08:00"
    APPROVAL_THRESHOLD_INR: int = 1000
    DAILY_DISCOUNT_BUDGET_INR: int = 50000

    def require(self, name: str) -> str:
        """Return a setting's value, or raise a friendly ConfigError if it is empty."""
        value = getattr(self, name, None)
        if value in (None, ""):
            raise ConfigError(
                f"'{name}' is not set. Open your .env file (copy it from .env.example if it "
                f"doesn't exist) and fill in {name}=... {SETUP_HELP}"
            )
        if name == "OFFERS_API_KEY" and value == "change-me":
            raise ConfigError(f"OFFERS_API_KEY is still 'change-me'. Create a real one (Step 8). {SETUP_HELP}")
        return str(value)

    def has(self, name: str) -> bool:
        """True if the setting has a non-empty value (handy for 'mock mode' checks)."""
        return bool(getattr(self, name, ""))

    def describe(self) -> dict[str, str]:
        """All settings as strings, with secrets masked — safe to print or log."""
        out = {}
        for f in fields(self):
            value = getattr(self, f.name)
            out[f.name] = mask(value) if f.name in SECRET_KEYS else str(value)
        return out

    def __repr__(self) -> str:  # so print(settings) never leaks a key
        return f"Settings({self.describe()})"


def load_settings(env_file: Path | None = ENV_FILE) -> Settings:
    """Read .env (if present) + environment variables and build a Settings object."""
    if env_file is not None and Path(env_file).exists():
        # override=False: real environment variables win over the file (useful for tests/CI).
        load_dotenv(env_file, override=False)

    data_source = _get_str("DATA_SOURCE", "sample").lower()
    if data_source not in ("sample", "synthetic", "external"):
        raise ConfigError(f"DATA_SOURCE must be sample, synthetic or external, got '{data_source}'. {SETUP_HELP}")

    db_path = Path(_get_str("DB_PATH", "data/app.db"))
    if not db_path.is_absolute():
        # Relative paths are relative to the project root, not to wherever you ran python from.
        db_path = PROJECT_ROOT / db_path

    return Settings(
        ANTHROPIC_API_KEY=_get_str("ANTHROPIC_API_KEY"),
        TELEGRAM_BOT_TOKEN=_get_str("TELEGRAM_BOT_TOKEN"),
        OFFERS_API_KEY=_get_str("OFFERS_API_KEY"),
        CLAUDE_MODEL_FAST=_get_str("CLAUDE_MODEL_FAST"),
        CLAUDE_MODEL_SMART=_get_str("CLAUDE_MODEL_SMART"),
        TELEGRAM_BOT_USERNAME=_get_str("TELEGRAM_BOT_USERNAME").lstrip("@"),
        TIMEZONE=_get_str("TIMEZONE", "Asia/Kolkata"),
        DB_PATH=db_path,
        DATA_SOURCE=data_source,
        EXTERNAL_SOURCE=_get_str("EXTERNAL_SOURCE"),
        OFFERS_API_URL=_get_str("OFFERS_API_URL", "http://127.0.0.1:8001"),
        MAIN_API_URL=_get_str("MAIN_API_URL", "http://127.0.0.1:8000"),
        MAX_DISCOUNT_PCT=_get_int("MAX_DISCOUNT_PCT", 40),
        MAX_OFFERS_PER_DAY=_get_int("MAX_OFFERS_PER_DAY", 1),
        MAX_OFFERS_PER_WEEK=_get_int("MAX_OFFERS_PER_WEEK", 3),
        QUIET_START=_get_str("QUIET_START", "22:00"),
        QUIET_END=_get_str("QUIET_END", "08:00"),
        APPROVAL_THRESHOLD_INR=_get_int("APPROVAL_THRESHOLD_INR", 1000),
        DAILY_DISCOUNT_BUDGET_INR=_get_int("DAILY_DISCOUNT_BUDGET_INR", 50000),
    )


# The single shared settings object everyone imports.
settings = load_settings()
