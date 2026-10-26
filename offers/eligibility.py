"""Step 2 of the Offer Pipeline: may we message this user about an offer right now?

    check_eligibility("U00001", "cart_abandon")  ->  GuardrailResult(allowed=True, reason="eligible")

Checks, in order (the first one that fails is the reason):
  1. consent      — user opted in (user_consent.opted_in = 1)
  2. telegram     — user linked the Telegram bot (telegram_links.active = 1 with a chat_id)
  3. quiet hours  — not between the user's quiet_start and quiet_end (or the .env default)
  4. daily cap    — fewer than MAX_OFFERS_PER_DAY offers delivered today
  5. weekly cap   — fewer than MAX_OFFERS_PER_WEEK (or the user's own lower limit) in the last 7 days
  6. segment      — at least one live offer for this trigger targets a segment the user is in
Every decision is written to audit_log.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path

from common.config import settings
from common.logging_utils import audit, now_local
from common.schemas import GuardrailResult, TriggerType
from offers import store
from offers.segments import list_live_offers


def _parse_hhmm(value: str | None, default: str) -> time:
    try:
        return time.fromisoformat(str(value or default).strip())
    except ValueError:
        return time.fromisoformat(default)


def in_quiet_hours(now: datetime, quiet_start: str | None = None, quiet_end: str | None = None) -> bool:
    """True if `now` is inside the quiet window. Handles windows that cross midnight (22:00 -> 08:00)."""
    start = _parse_hhmm(quiet_start, settings.QUIET_START)
    end = _parse_hhmm(quiet_end, settings.QUIET_END)
    t = now.time()
    if start == end:
        return False  # no quiet window
    if start < end:  # same-day window, e.g. 13:00 -> 15:00
        return start <= t < end
    return t >= start or t < end  # overnight window


def count_delivered(user_id: str, since: datetime, db_path: str | Path | None = None) -> int:
    """How many offers went out to this user since `since` (blocked/failed don't count)."""
    sends = store.load_sends(user_id, db_path)
    sends = sends[sends["status"].isin(store.DELIVERED_STATUSES) & sends["when"].notna()]
    return int((sends["when"] >= since.isoformat(timespec="seconds")).sum())


def _decide(user_id: str, trigger_type: str, now: datetime, db_path) -> GuardrailResult:
    try:
        TriggerType(trigger_type)
    except ValueError:
        return GuardrailResult(allowed=False, reason=f"unknown trigger_type '{trigger_type}'")

    consent = store.get_row("user_consent", user_id, db_path)
    if not consent or int(consent.get("opted_in") or 0) != 1:
        return GuardrailResult(allowed=False, reason="user has not opted in to offers")

    link = store.get_row("telegram_links", user_id, db_path)
    if not link or int(link.get("active") or 0) != 1 or not link.get("chat_id"):
        return GuardrailResult(allowed=False, reason="user has not linked Telegram")

    if in_quiet_hours(now, consent.get("quiet_start"), consent.get("quiet_end")):
        return GuardrailResult(allowed=False, reason="quiet hours for this user")

    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today = count_delivered(user_id, start_of_day, db_path)
    if today >= settings.MAX_OFFERS_PER_DAY:
        return GuardrailResult(allowed=False, reason=f"daily cap reached ({today}/{settings.MAX_OFFERS_PER_DAY})")

    # The user may choose a lower weekly limit than the store's, never a higher one.
    weekly_cap = settings.MAX_OFFERS_PER_WEEK
    if consent.get("max_offers_per_week") not in (None, ""):
        weekly_cap = min(weekly_cap, int(consent["max_offers_per_week"]))
    week = count_delivered(user_id, now - timedelta(days=7), db_path)
    if week >= weekly_cap:
        return GuardrailResult(allowed=False, reason=f"weekly cap reached ({week}/{weekly_cap})")

    if not list_live_offers(user_id, trigger_type, now=now, db_path=db_path):
        return GuardrailResult(allowed=False, reason=f"no live {trigger_type} offer for this user's segment")

    return GuardrailResult(allowed=True, reason="eligible")


def check_eligibility(user_id: str, trigger_type: str, *, now: datetime | None = None,
                      db_path: str | Path | None = None) -> GuardrailResult:
    """Module-contract function. `now` / `db_path` are optional (for tests and the scheduler)."""
    now = now or now_local()
    result = _decide(user_id, str(getattr(trigger_type, "value", trigger_type)), now, db_path)
    audit("offers", user_id, "check_eligibility", "allowed" if result.allowed else "blocked",
          result.reason, {"trigger_type": str(getattr(trigger_type, "value", trigger_type))}, db_path=db_path)
    return result
