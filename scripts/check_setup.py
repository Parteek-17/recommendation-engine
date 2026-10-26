"""Health check: prints ✅ / ❌ for everything the project needs.

Run:  python scripts/check_setup.py            # offline checks only
      python scripts/check_setup.py --online   # also test the Claude key and Telegram token

Keys are always shown masked (sk-...xyz). It's safe to copy this output when asking for help.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK, BAD, WARN = "✅", "❌", "⚠️ "

# import name -> pip name (they differ for some packages)
PACKAGES = {
    "pandas": "pandas", "numpy": "numpy", "pydantic": "pydantic", "dotenv": "python-dotenv",
    "requests": "requests", "yaml": "pyyaml", "tzdata": "tzdata", "faker": "Faker",
    "sklearn": "scikit-learn", "scipy": "scipy", "implicit": "implicit", "lightgbm": "lightgbm",
    "fastapi": "fastapi", "uvicorn": "uvicorn", "anthropic": "anthropic", "telegram": "python-telegram-bot",
    "apscheduler": "APScheduler", "streamlit": "streamlit", "pytest": "pytest",
}
# Without these, Phase 0 code can't run at all.
CORE = {"pandas", "numpy", "pydantic", "dotenv"}

failures = 0


def report(ok: bool, label: str, hint: str = "", warn_only: bool = False) -> None:
    """Print one line; count real failures (warnings don't count)."""
    global failures
    if ok:
        print(f"{OK} {label}")
    elif warn_only:
        print(f"{WARN} {label}" + (f"  -> {hint}" if hint else ""))
    else:
        failures += 1
        print(f"{BAD} {label}" + (f"  -> {hint}" if hint else ""))


def check_python() -> None:
    v = sys.version_info
    report(v >= (3, 11), f"Python {v.major}.{v.minor}.{v.micro}", "install Python 3.11+ (SETUP_GUIDE Step 1)")
    in_venv = sys.prefix != sys.base_prefix
    report(in_venv, "virtual environment active", r"run .\.venv\Scripts\Activate.ps1 (Step 3)", warn_only=True)


def check_packages() -> bool:
    missing_core = False
    for module, pip_name in PACKAGES.items():
        found = importlib.util.find_spec(module) is not None
        if module in CORE:
            missing_core |= not found
        report(found, f"package {pip_name}", "pip install -r requirements.txt", warn_only=module not in CORE)
    return not missing_core


def check_env_and_keys() -> None:
    from common.config import ENV_FILE, mask, settings

    report(ENV_FILE.exists(), ".env file exists", "copy .env.example .env (Step 5)")
    for name in ("ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN"):
        value = getattr(settings, name)
        report(bool(value), f"{name} = {mask(value)}", "optional for now; see SETUP_GUIDE (mock mode without it)",
               warn_only=True)
    key = settings.OFFERS_API_KEY
    report(bool(key) and key != "change-me", f"OFFERS_API_KEY = {mask(key)}",
           "set a random value (Step 8)", warn_only=True)
    for name in ("CLAUDE_MODEL_FAST", "CLAUDE_MODEL_SMART", "TELEGRAM_BOT_USERNAME"):
        report(settings.has(name), f"{name} = {getattr(settings, name) or '(empty)'}", "fill in .env", warn_only=True)
    print(f"   DATA_SOURCE={settings.DATA_SOURCE}  TIMEZONE={settings.TIMEZONE}  DB_PATH={settings.DB_PATH}")


def check_db() -> None:
    from common.config import settings
    from common.db import TABLES, get_data_level, table_counts

    exists = settings.DB_PATH.exists()
    report(exists, f"database exists ({settings.DB_PATH.name})", "python scripts/init_db.py (Step 10)")
    if not exists:
        return
    counts = table_counts()
    missing = [t for t, n in counts.items() if n < 0]
    report(not missing, f"all {len(TABLES)} tables present", f"missing {missing}; run python scripts/init_db.py")
    report(counts.get("events", 0) > 0, f"data loaded ({counts.get('events', 0)} events, "
           f"{counts.get('products', 0)} products) — level {get_data_level()}", "python scripts/init_db.py")


def check_anthropic_online() -> None:
    from common.config import settings

    if not settings.ANTHROPIC_API_KEY:
        report(False, "Anthropic API (skipped: no key)", warn_only=True)
        return
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        client.messages.create(model=settings.CLAUDE_MODEL_FAST or "claude-haiku-5-5", max_tokens=5,
                               messages=[{"role": "user", "content": "Say ok"}])
        report(True, f"Anthropic API works (model {settings.CLAUDE_MODEL_FAST})")
    except ImportError:
        report(False, "Anthropic API", "pip install anthropic")
    except Exception as exc:  # show the error type/message, never the key
        report(False, f"Anthropic API: {type(exc).__name__}: {str(exc)[:150]}", "see Troubleshooting in SETUP_GUIDE")


def check_telegram_online() -> None:
    from common.config import settings

    if not settings.TELEGRAM_BOT_TOKEN:
        report(False, "Telegram bot (skipped: no token)", warn_only=True)
        return
    try:
        import requests

        resp = requests.get(f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/getMe", timeout=10)
        data = resp.json()
        if data.get("ok"):
            report(True, f"Telegram bot works (@{data['result'].get('username')})")
        else:
            report(False, f"Telegram: {data.get('description', resp.status_code)}", "copy the token again from BotFather")
    except Exception as exc:
        # Don't print exc for requests errors: the URL in it contains the token.
        report(False, f"Telegram: could not connect ({type(exc).__name__})", "check internet connection")


def main() -> None:
    parser = argparse.ArgumentParser(description="Project health check")
    parser.add_argument("--online", action="store_true", help="also test Claude API key and Telegram token")
    args = parser.parse_args()

    print("== Python ==")
    check_python()
    print("\n== Packages ==")
    if not check_packages():
        print(f"\n{BAD} Core packages missing — install them first: pip install -r requirements.txt")
        sys.exit(1)
    print("\n== Settings (.env) ==")
    try:
        check_env_and_keys()
    except Exception as exc:  # e.g. ConfigError with a friendly message
        report(False, f"settings: {exc}")
        sys.exit(1)
    print("\n== Database ==")
    check_db()
    if args.online:
        print("\n== Online checks ==")
        check_anthropic_online()
        check_telegram_online()
    else:
        print("\n(Skipping online checks. Add --online to test the Claude key and Telegram token.)")

    print()
    if failures:
        print(f"{BAD} {failures} problem(s) found. Fixes: docs/SETUP_GUIDE.md -> Troubleshooting")
        sys.exit(1)
    print(f"{OK} All required checks passed. ({WARN.strip()} lines are optional for now.)")


if __name__ == "__main__":
    # Windows consoles sometimes can't print emoji; fall back gracefully instead of crashing.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
