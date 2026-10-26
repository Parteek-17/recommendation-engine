"""Logging helpers.

    from common.logging_utils import get_logger, audit
    log = get_logger(__name__)
    log.info("Training ALS...")
    audit("offers", "U00001", "send_offer", "blocked", "frequency cap reached", {"offer_id": "OF0001"})

`get_logger` = normal messages for developers (printed to the console).
`audit`      = important decisions (guardrails, sends, blocks) saved to the `audit_log`
               table so we can always answer "why did / didn't this happen?".
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from common.config import settings

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def get_logger(name: str = "app") -> logging.Logger:
    """Return a console logger. Call once per file: log = get_logger(__name__)."""
    logger = logging.getLogger(name)
    if not logging.getLogger().handlers and not logger.handlers:
        # Configure the root logger once; every module's logger then shares the same format.
        logging.basicConfig(level=logging.INFO, format=_FORMAT, datefmt="%H:%M:%S")
    return logger


def now_local() -> datetime:
    """Current time in the project TIMEZONE, as a plain (naive) datetime.

    All timestamps in our data are local time WITHOUT a '+05:30' suffix, so they
    sort and compare correctly as text. Use this instead of datetime.now().
    """
    try:
        tz = ZoneInfo(settings.TIMEZONE)
    except Exception:  # tz database missing (pip install tzdata) -> fall back to computer's clock
        return datetime.now()
    return datetime.now(tz).replace(tzinfo=None)


def now_iso() -> str:
    """Current local time as ISO-8601 text, e.g. '2026-09-01T19:30:00'."""
    return now_local().isoformat(timespec="seconds")


def audit(
    component: str,
    user_id: str | None,
    action: str,
    decision: str,
    reason: str = "",
    details: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> None:
    """Write one row to `audit_log`.

    component: which module decided (e.g. "offers", "llm", "bot", "pipeline")
    action:    what was attempted (e.g. "send_offer", "chat_reply")
    decision:  outcome (e.g. "allowed", "blocked", "fallback", "sent")
    reason:    short human-readable why
    details:   extra info as a dict (stored as JSON). Never put secrets here.

    Auditing must never crash the main flow, so DB errors are logged, not raised.
    """
    # Imported here to avoid a circular import (db imports config, config is shared).
    from common.db import get_connection, init_db

    details_json = json.dumps(details or {}, ensure_ascii=False, default=str)
    try:
        init_db(db_path)  # cheap (IF NOT EXISTS); makes audit work even on a brand-new DB
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO audit_log (timestamp, component, user_id, action, decision, reason, details_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (now_iso(), component, user_id, action, decision, reason, details_json),
            )
    except Exception as exc:
        get_logger("audit").error("Could not write audit row (%s %s %s): %s", component, action, decision, exc)
