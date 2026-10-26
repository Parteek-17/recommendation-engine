"""Account linking: connect one of our users (U00001) to a Telegram chat.

How it works (deep links):
1. Our website calls `create_link_token("U00001")` and shows the user a link like
   https://t.me/parvaaz_shop_bot?start=3f9a..._20261023101500
2. The user taps it. Telegram opens our bot and shows a **Start** button.
3. Pressing Start makes Telegram send our bot the message "/start 3f9a..._20261023101500".
4. The bot calls `link_chat(token, chat_id)`: now we know chat 123456 = user U00001,
   and the user has opted in to offers (pressing Start is the opt-in).

Why a token and not just "?start=U00001"? Anyone could type someone else's user ID.
A random token can only come from that user's own account page.

Token rules: single-use, valid for 24 hours. The time it was issued is part of the token
itself (the part after "_"), so we don't need an extra column to check expiry. Tampering
with that part is pointless: we look up the WHOLE token in `telegram_links`.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from bot import store
from common.config import settings
from common.logging_utils import audit, get_logger, now_local

log = get_logger(__name__)

TOKEN_TTL = timedelta(hours=24)
_TIME_FORMAT = "%Y%m%d%H%M%S"


@dataclass
class LinkResult:
    """What happened when someone pressed Start with a token."""

    ok: bool
    user_id: str | None
    reason: str  # "linked" | "already_linked" | "invalid" | "expired" | "used"


def _new_token(now: datetime) -> str:
    # 16 random hex chars + issue time. Telegram allows A-Z a-z 0-9 _ - and max 64 chars.
    return f"{secrets.token_hex(8)}_{now.strftime(_TIME_FORMAT)}"


def token_issued_at(token: str) -> datetime | None:
    """When the token was made, or None for old-style tokens without a time part (sample data)."""
    _, _, stamp = token.rpartition("_")
    try:
        return datetime.strptime(stamp, _TIME_FORMAT)
    except ValueError:
        return None


def deep_link(token: str) -> str:
    username = settings.TELEGRAM_BOT_USERNAME
    if not username:
        log.warning("TELEGRAM_BOT_USERNAME is empty in .env, the link will not open the right bot")
        username = "your_bot"
    return f"https://t.me/{username}?start={token}"


def create_link_token(user_id: str, now: datetime | None = None) -> str:
    """Make a fresh single-use token for `user_id` and return the deep link to show them.

    If the user is already linked, their current link keeps working until the new
    token is used (then it moves to the new chat).
    """
    if not user_id or not store.user_exists(user_id):
        raise ValueError(f"Unknown user_id: {user_id!r}")
    token = _new_token(now or now_local())
    with store.db() as conn:
        exists = conn.execute("SELECT 1 FROM telegram_links WHERE user_id = ?", (user_id,)).fetchone()
        if exists:
            conn.execute("UPDATE telegram_links SET link_token = ? WHERE user_id = ?", (token, user_id))
        else:
            conn.execute(
                "INSERT INTO telegram_links (user_id, chat_id, link_token, linked_at, active) "
                "VALUES (?, NULL, ?, NULL, 0)", (user_id, token),
            )
    audit("bot", user_id, "create_link_token", "created", "deep link issued")
    return deep_link(token)


def _already_used(row: dict, issued: datetime | None) -> bool:
    """A token is used once its row has been linked AFTER the token was issued."""
    if not row["chat_id"] or not row["linked_at"]:
        return False
    if issued is None:
        return True  # old-style token on a linked row: it was the one that linked it
    return row["linked_at"] >= issued.isoformat(timespec="seconds")


def link_chat(token: str, chat_id: int | str, now: datetime | None = None) -> LinkResult:
    """Handle '/start <token>' from `chat_id`. Saves the link and opts the user in."""
    now = now or now_local()
    chat_id = str(chat_id)
    with store.db() as conn:
        row = conn.execute("SELECT * FROM telegram_links WHERE link_token = ?", (token,)).fetchone()
    if row is None:
        audit("bot", None, "link_chat", "rejected", "invalid token")
        return LinkResult(False, None, "invalid")
    row = dict(row)
    user_id = row["user_id"]
    issued = token_issued_at(token)

    if _already_used(row, issued):
        if row["chat_id"] == chat_id and row["active"]:
            return LinkResult(True, user_id, "already_linked")
        audit("bot", user_id, "link_chat", "rejected", "token already used")
        return LinkResult(False, user_id, "used")
    if issued is not None and now - issued > TOKEN_TTL:
        audit("bot", user_id, "link_chat", "rejected", "token expired")
        return LinkResult(False, user_id, "expired")

    with store.db() as conn:
        # One Telegram chat belongs to one shopper: unlink it from anyone else first.
        conn.execute(
            "UPDATE telegram_links SET active = 0, chat_id = NULL WHERE chat_id = ? AND user_id != ?",
            (chat_id, user_id),
        )
        conn.execute(
            "UPDATE telegram_links SET chat_id = ?, linked_at = ?, active = 1 WHERE user_id = ?",
            (chat_id, now.isoformat(timespec="seconds"), user_id),
        )
    store.set_opt_in(user_id, True)
    audit("bot", user_id, "link_chat", "linked", "deep link used")  # chat_id is personal data: not logged
    return LinkResult(True, user_id, "linked")
