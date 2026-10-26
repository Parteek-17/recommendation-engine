"""Checks run by scripts/run_all.* before starting anything.

    python scripts/preflight.py            # check + fix what can be fixed safely
    python scripts/preflight.py --check    # only report, change nothing

1. .env exists (copied from .env.example if not)
2. OFFERS_API_KEY is a real value. It's our OWN internal password between the pipeline and
   the Offers API, so if it is empty / 'change-me' we create a random one in .env.
   The value is never printed.
3. The database has data (else: python scripts/init_db.py)
4. Trained models exist for this DATA_SOURCE (else: python -m reco.train)
5. Telegram / Claude keys: only reported (bot is skipped / LLM runs in mock mode without them)

Exit code 0 = OK to start, 1 = something must be fixed first.
"""

from __future__ import annotations

import argparse
import pickle
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
ENV, ENV_EXAMPLE = ROOT / ".env", ROOT / ".env.example"


def _env_value(text: str, name: str) -> str:
    match = re.search(rf"^{name}=(.*)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def ensure_env(fix: bool) -> bool:
    if ENV.exists():
        return True
    if not fix:
        print("  [!!] .env is missing (copy .env.example to .env)")
        return False
    shutil.copy(ENV_EXAMPLE, ENV)
    print("  [ok] created .env from .env.example")
    return True


def ensure_offers_key(fix: bool) -> bool:
    text = ENV.read_text(encoding="utf-8")
    if _env_value(text, "OFFERS_API_KEY") not in ("", "change-me"):
        print("  [ok] OFFERS_API_KEY is set")
        return True
    if not fix:
        print("  [!!] OFFERS_API_KEY is empty or 'change-me'")
        return False
    key = secrets.token_urlsafe(32)
    line = f"OFFERS_API_KEY={key}"
    if re.search(r"^OFFERS_API_KEY=.*$", text, flags=re.MULTILINE):
        text = re.sub(r"^OFFERS_API_KEY=.*$", line, text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
    ENV.write_text(text, encoding="utf-8")
    print("  [ok] created a random OFFERS_API_KEY in .env (internal key, not shown)")
    return True


def check_data_and_models(fix: bool) -> bool:
    # Import after .env is final so settings see the right values.
    from common.config import settings
    from common.db import get_data_level, table_counts

    counts = table_counts()
    if counts.get("events", -1) <= 0:
        if not fix:
            print("  [!!] database is empty: run python scripts/init_db.py")
            return False
        print("  ...  database is empty, loading DATA_SOURCE =", settings.DATA_SOURCE)
        subprocess.check_call([sys.executable, str(ROOT / "scripts" / "init_db.py")], cwd=ROOT)
        counts = table_counts()
    print(f"  [ok] data: DATA_SOURCE={settings.DATA_SOURCE}, level {get_data_level()}, "
          f"{counts['users']:,} users, {counts['products']:,} products, {counts['events']:,} events")

    model = ROOT / "models" / "reco_bundle.pkl"
    trained_for = None
    if model.exists():
        try:
            with open(model, "rb") as fh:
                trained_for = pickle.load(fh).get("data_source")
        except Exception:
            trained_for = None
    if trained_for != settings.DATA_SOURCE:
        if not fix:
            print("  [!!] no trained models for this DATA_SOURCE: run python -m reco.train")
            return False
        print("  ...  training the recommendation models (one-off, a few minutes on full data)")
        subprocess.check_call([sys.executable, "-m", "reco.train"], cwd=ROOT)
    print("  [ok] trained models found")
    return True


def ensure_persona_pool(fix: bool) -> None:
    """Demo shoppers for classmates (bot/personas.py). Missing pool = slow first taps, not an error."""
    from bot import personas

    if all(personas._pool(p.key) for p in personas.PERSONAS):
        print("  [ok] demo shopper pool ready")
        return
    if not fix:
        print("  [--] demo shopper pool empty: run python -m bot.personas (taps will be slow until then)")
        return
    print("  ...  finding demo shoppers for the Telegram personas (one-off, ~1-3 min)")
    subprocess.check_call([sys.executable, "-m", "bot.personas"], cwd=ROOT)


def report_optional_keys() -> None:
    from common.config import settings

    if settings.has("TELEGRAM_BOT_TOKEN") and settings.has("TELEGRAM_BOT_USERNAME"):
        print("  [ok] Telegram bot token + username set")
    else:
        print("  [--] TELEGRAM_BOT_TOKEN / TELEGRAM_BOT_USERNAME not set: the bot will NOT start, "
              "offers can't be delivered (docs/SETUP_GUIDE.md, Step 7)")
    if settings.has("ANTHROPIC_API_KEY"):
        print("  [ok] Claude API key set (real LLM mode)")
    else:
        print("  [--] ANTHROPIC_API_KEY not set: LLM runs in mock mode (still works, canned wording)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-start checks for run_all.")
    parser.add_argument("--check", action="store_true", help="only report, don't change anything")
    args = parser.parse_args()
    fix = not args.check
    print("Pre-flight checks")
    ok = ensure_env(fix) and ensure_offers_key(fix) and check_data_and_models(fix)
    if ok:
        ensure_persona_pool(fix)
        report_optional_keys()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
