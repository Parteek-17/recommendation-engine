"""Render free plan: the Offers API, the offer scheduler and the Telegram bot in ONE process.

    python deploy/render/backend.py

On a laptop these run as three programs (scripts/run_all.*). Render's free web service has
512 MB of memory, and each separate Python process loads pandas, the rec engine and the LLM
client again (~190 MB each). Sharing one process keeps the whole store under the limit.
The Admin dashboard (Streamlit) still runs as its own process: see deploy/render/start.sh.
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.config import settings  # noqa: E402
from common.logging_utils import get_logger  # noqa: E402

log = get_logger("render")


def start_offers_api() -> None:
    """Offers API on 127.0.0.1:8001 in a background thread (only the pipeline calls it)."""
    import uvicorn

    from offers.api import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8001, log_level="warning"))
    threading.Thread(target=server.run, name="offers-api", daemon=True).start()
    for _ in range(60):  # the pipeline needs it before the first offer round
        if server.started:
            log.info("Offers API ready on :8001")
            return
        time.sleep(0.5)
    log.warning("Offers API did not report ready after 30 s")


def start_scheduler() -> None:
    """Offer rounds every 15 min. No nightly retrain: the free CPU is too slow for it."""
    from apscheduler.schedulers.background import BackgroundScheduler

    from pipeline.scheduler import INTERVAL_MINUTES, _tz, run_round

    scheduler = BackgroundScheduler(timezone=_tz())
    scheduler.add_job(run_round, "interval", minutes=INTERVAL_MINUTES, next_run_time=datetime.now(_tz()),
                      id="offer_round", max_instances=1, coalesce=True)
    scheduler.start()
    log.info("Scheduler started: offer round every %s min", INTERVAL_MINUTES)


def main() -> None:
    start_offers_api()
    start_scheduler()
    if settings.has("TELEGRAM_BOT_TOKEN"):
        from bot.main import main as run_bot

        run_bot()  # long polling; needs the main thread (signal handling)
    else:
        log.warning("No TELEGRAM_BOT_TOKEN: bot not started")
        threading.Event().wait()


if __name__ == "__main__":
    main()
