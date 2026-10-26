"""Which segments a user belongs to, and which live offers they can see.

Segments are built ONLY from behaviour (orders, budget band), location tier and account
age. We never read gender, religion or any other sensitive attribute here — that's a
hard rule (CLAUDE.md, Phase 3 prompt), and rules.check_segment() blocks offers that try.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from common.logging_utils import get_logger, now_local
from common.schemas import Offer
from offers import store
from offers.rules import NEW_USER_DAYS, check_segment, segment_matches

# Use the real recommendation engine once Phase 2 exports it; until then the stub.
try:
    from reco import build_user_context
except ImportError:
    from common.stubs import build_user_context

log = get_logger(__name__)


def budget_band(user_id: str) -> str:
    """'low' | 'mid' | 'high' from the rec engine. Falls back to 'mid' so offers never crash on it."""
    try:
        return build_user_context(user_id).budget_band or "mid"
    except Exception as exc:  # rec engine problem must not stop the offer engine
        log.warning("build_user_context failed for %s (%s); using 'mid'", user_id, exc)
        return "mid"


def user_segments(user_id: str, now: datetime | None = None, db_path: str | Path | None = None) -> set[str]:
    """All named segments this user is in, e.g. {'all', 'tier3', 'returning', 'price_sensitive'}."""
    now = now or now_local()
    segments = {"all"}
    user = store.get_row("users", user_id, db_path)
    if user:
        if user.get("city_tier"):
            segments.add(f"tier{int(user['city_tier'])}")
        signup = user.get("signup_date")
        if signup and datetime.fromisoformat(str(signup)) >= now - timedelta(days=NEW_USER_DAYS):
            segments.add("new_users")
    if store.order_count(user_id, db_path) > 0:
        segments.add("returning")
    band = budget_band(user_id)
    if band == "low":
        segments.add("price_sensitive")
    elif band == "high":
        segments.add("premium")
    return segments


def list_live_offers(user_id: str | None = None, trigger_type: str | None = None,
                     now: datetime | None = None, db_path: str | Path | None = None) -> list[Offer]:
    """Active offers valid right now. With `user_id`: only offers whose segment includes that user.

    This is what GET /offers/live returns. It does NOT apply the per-user guardrails
    (caps, dedup, budget...) — those run later in apply_offer_guardrails().
    """
    now = now or now_local()
    offers = [o for o in store.load_offers(db_path) if store.is_live(o, now)]
    if trigger_type:
        offers = [o for o in offers if o.trigger_type in (None, trigger_type)]
    if user_id:
        segs = user_segments(user_id, now, db_path)
        offers = [o for o in offers if check_segment(o.segment).allowed and segment_matches(o.segment, user_id, segs)]
    return offers
