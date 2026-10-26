"""Small database helpers used only by the bot.

Everything here is plain, synchronous SQLite through `common.db.get_connection`, so the
same helpers work from the bot process, from the pipeline process and from tests.

Tables we touch (Data Contract): telegram_links, user_consent, events, sessions,
offer_sends, offers, products, categories, audit_log.

One extra table is owned by the bot (prefixed `bot_` so nobody mistakes it for a
contract table): `bot_user_prefs` stores the gift-mode switch, which has no column in
the Data Contract.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Iterator

from common.config import settings
from common.db import get_connection, init_db
from common.logging_utils import audit, now_local

# Engagement statuses only ever move forward: a "redeemed" offer must never go back to "opened".
_STATUS_ORDER = {"queued": 0, "sent": 1, "opened": 2, "clicked": 3, "redeemed": 4}

# Columns a user may change through /settings (anything else is refused).
_CONSENT_EDITABLE = ("opted_in", "quiet_start", "quiet_end", "preferred_categories", "max_offers_per_week")

# A Telegram "session" = clicks from one user that are less than this far apart.
SESSION_GAP = timedelta(minutes=30)

_ready_dbs: set[str] = set()


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    """Open the DB, commit on success, and ALWAYS close (Windows keeps files locked otherwise)."""
    path = str(settings.DB_PATH)
    if path not in _ready_dbs:
        init_db()
        with get_connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS bot_user_prefs "
                "(user_id TEXT PRIMARY KEY, gift_mode INTEGER DEFAULT 0, updated_at TEXT)"
            )
        _ready_dbs.add(path)
    conn = get_connection()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _iso(now: datetime | None = None) -> str:
    return (now or now_local()).isoformat(timespec="seconds")


def next_id(conn: sqlite3.Connection, table: str, column: str, prefix: str, width: int) -> str:
    """Next ID in a fixed-width series, e.g. E00000041 after E00000040.

    Fixed width means the text maximum is also the numeric maximum.
    `table`/`column` are always constants from this file, never user input.
    """
    row = conn.execute(f"SELECT MAX({column}) FROM {table} WHERE {column} LIKE ?", (prefix + "%",)).fetchone()
    last = row[0]
    number = int(last[len(prefix):]) + 1 if last and last[len(prefix):].isdigit() else 1
    return f"{prefix}{number:0{width}d}"


# ---------------------------------------------------------------------------
# Users, links and consent
# ---------------------------------------------------------------------------


def user_exists(user_id: str) -> bool:
    """True if the user is in `users` — or if the data has no users table at all (L1/L2 data)."""
    with db() as conn:
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            return True
        return conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone() is not None


def user_for_chat(chat_id: int | str) -> str | None:
    """Which of our users owns this Telegram chat (only active links count)."""
    with db() as conn:
        row = conn.execute(
            "SELECT user_id FROM telegram_links WHERE chat_id = ? AND active = 1", (str(chat_id),)
        ).fetchone()
    return row["user_id"] if row else None


def chat_for_user(user_id: str) -> str | None:
    """The Telegram chat of a user, or None if they never linked / blocked the bot."""
    with db() as conn:
        row = conn.execute(
            "SELECT chat_id FROM telegram_links WHERE user_id = ? AND active = 1 AND chat_id IS NOT NULL "
            "AND chat_id != ''", (user_id,)
        ).fetchone()
    return row["chat_id"] if row else None


def deactivate_link(user_id: str) -> None:
    """Mark a link inactive (user blocked the bot). The row stays for history."""
    with db() as conn:
        conn.execute("UPDATE telegram_links SET active = 0 WHERE user_id = ?", (user_id,))


def get_consent(user_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM user_consent WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def update_consent(user_id: str, **changes) -> None:
    """Create or update the user's consent row. Unknown column names raise ValueError."""
    bad = [c for c in changes if c not in _CONSENT_EDITABLE]
    if bad:
        raise ValueError(f"Not editable: {bad}")
    with db() as conn:
        exists = conn.execute("SELECT 1 FROM user_consent WHERE user_id = ?", (user_id,)).fetchone()
        if not exists:
            # New users start with the system defaults from .env.
            conn.execute(
                "INSERT INTO user_consent (user_id, opted_in, quiet_start, quiet_end, preferred_categories, "
                "max_offers_per_week) VALUES (?, 0, ?, ?, '', ?)",
                (user_id, settings.QUIET_START, settings.QUIET_END, settings.MAX_OFFERS_PER_WEEK),
            )
        for column, value in changes.items():
            conn.execute(f"UPDATE user_consent SET {column} = ? WHERE user_id = ?", (value, user_id))


def set_opt_in(user_id: str, opted_in: bool) -> None:
    update_consent(user_id, opted_in=1 if opted_in else 0)
    audit("bot", user_id, "consent", "opt_in" if opted_in else "opt_out", "user command via Telegram")


def is_opted_in(user_id: str) -> bool:
    consent = get_consent(user_id)
    return bool(consent and int(consent["opted_in"] or 0) == 1)


def _parse_hhmm(text: str | None, default: str) -> tuple[int, int]:
    try:
        hours, minutes = (text or default).split(":")
        return int(hours), int(minutes)
    except ValueError:
        hours, minutes = default.split(":")
        return int(hours), int(minutes)


def in_quiet_hours(now: datetime, quiet_start: str | None, quiet_end: str | None) -> bool:
    """True if `now` falls in the quiet window. Handles windows that cross midnight (22:00–08:00)."""
    start = _parse_hhmm(quiet_start, settings.QUIET_START)
    end = _parse_hhmm(quiet_end, settings.QUIET_END)
    current = (now.hour, now.minute)
    if start == end:
        return False  # empty window
    if start < end:
        return start <= current < end  # same-day window, e.g. 13:00–15:00
    return current >= start or current < end  # crosses midnight


def is_quiet_time(user_id: str, now: datetime | None = None) -> bool:
    """Quiet hours from the user's settings, falling back to the global ones in .env."""
    consent = get_consent(user_id) or {}
    return in_quiet_hours(now or now_local(), consent.get("quiet_start"), consent.get("quiet_end"))


# ---------------------------------------------------------------------------
# Gift mode (bot-owned table)
# ---------------------------------------------------------------------------


def get_gift_mode(user_id: str) -> bool:
    with db() as conn:
        row = conn.execute("SELECT gift_mode FROM bot_user_prefs WHERE user_id = ?", (user_id,)).fetchone()
    return bool(row and row["gift_mode"])


def set_gift_mode(user_id: str, on: bool) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO bot_user_prefs (user_id, gift_mode, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET gift_mode = excluded.gift_mode, updated_at = excluded.updated_at",
            (user_id, 1 if on else 0, _iso()),
        )


# ---------------------------------------------------------------------------
# Catalog lookups (prices, titles etc. ALWAYS come from the DB, never from the LLM)
# ---------------------------------------------------------------------------


def get_product(product_id: str | None) -> dict | None:
    if not product_id:
        return None
    with db() as conn:
        row = conn.execute("SELECT * FROM products WHERE product_id = ?", (product_id,)).fetchone()
    return dict(row) if row else None


def get_offer(offer_id: str | None) -> dict | None:
    if not offer_id:
        return None
    with db() as conn:
        row = conn.execute("SELECT * FROM offers WHERE offer_id = ?", (offer_id,)).fetchone()
    return dict(row) if row else None


def category_name(category_id: str | None) -> str:
    if not category_id:
        return ""
    with db() as conn:
        row = conn.execute("SELECT name FROM categories WHERE category_id = ?", (category_id,)).fetchone()
    return row["name"] if row else category_id


def top_level_categories() -> list[tuple[str, str]]:
    """[(category_id, name)] of the main categories (no parent) — used by /settings."""
    with db() as conn:
        rows = conn.execute(
            "SELECT category_id, name FROM categories WHERE parent_id IS NULL OR parent_id = '' ORDER BY category_id"
        ).fetchall()
    return [(r["category_id"], r["name"]) for r in rows]


# ---------------------------------------------------------------------------
# Events + sessions (button clicks are written back as events, source='telegram')
# ---------------------------------------------------------------------------


def _telegram_session(conn: sqlite3.Connection, user_id: str, now: datetime) -> str:
    """Reuse the user's recent Telegram session, or start a new one."""
    since = _iso(now - SESSION_GAP)
    row = conn.execute(
        "SELECT session_id FROM sessions WHERE user_id = ? AND device = 'telegram' AND start_time >= ? "
        "ORDER BY start_time DESC LIMIT 1", (user_id, since)
    ).fetchone()
    if row:
        return row["session_id"]
    session_id = next_id(conn, "sessions", "session_id", "S", 7)
    is_gift = conn.execute("SELECT gift_mode FROM bot_user_prefs WHERE user_id = ?", (user_id,)).fetchone()
    conn.execute(
        "INSERT INTO sessions (session_id, user_id, start_time, device, is_gift) VALUES (?, ?, ?, 'telegram', ?)",
        (session_id, user_id, _iso(now), 1 if is_gift and is_gift[0] else 0),
    )
    return session_id


def log_event(user_id: str, product_id: str, event_type: str, now: datetime | None = None) -> str:
    """Write one row to `events` with source='telegram'. Returns the new event_id."""
    now = now or now_local()
    # Another process (the API) may grab the same "next" ID at the same moment -> just retry.
    for _ in range(5):
        try:
            with db() as conn:
                session_id = _telegram_session(conn, user_id, now)
                event_id = next_id(conn, "events", "event_id", "E", 8)
                conn.execute(
                    "INSERT INTO events (event_id, user_id, product_id, session_id, event_type, timestamp, source, "
                    "search_query) VALUES (?, ?, ?, ?, ?, ?, 'telegram', NULL)",
                    (event_id, user_id, product_id, session_id, event_type, _iso(now)),
                )
            return event_id
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError("Could not create a unique event_id after 5 tries")


def record_not_interested(user_id: str, reason: str, product_id: str | None = None,
                          offer_id: str | None = None) -> None:
    """Store a 'Not interested because…' answer for the reco engine.

    There is no feedback table in the Data Contract yet, so it goes to audit_log with a
    fixed shape (see bot/README.md and phases/NOTES.md):
    component='bot', action='not_interested', decision=<reason>, details={product_id, offer_id}.
    """
    audit("bot", user_id, "not_interested", reason, "user feedback via Telegram",
          {"product_id": product_id, "offer_id": offer_id})


# ---------------------------------------------------------------------------
# offer_sends
# ---------------------------------------------------------------------------


def create_offer_send(user_id: str, offer_id: str, message: str, trigger_type: str | None,
                      now: datetime | None = None) -> str:
    """Insert a new `offer_sends` row with status 'queued'. Returns its send_id."""
    for _ in range(5):
        try:
            with db() as conn:
                send_id = next_id(conn, "offer_sends", "send_id", "SN", 6)
                conn.execute(
                    "INSERT INTO offer_sends (send_id, user_id, offer_id, trigger_type, message, channel, status, "
                    "sent_at, updated_at) VALUES (?, ?, ?, ?, ?, 'telegram', 'queued', NULL, ?)",
                    (send_id, user_id, offer_id, trigger_type, message, _iso(now)),
                )
            return send_id
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError("Could not create a unique send_id after 5 tries")


def get_send(send_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM offer_sends WHERE send_id = ?", (send_id,)).fetchone()
    return dict(row) if row else None


def set_send_status(send_id: str, status: str, now: datetime | None = None) -> bool:
    """Change an offer_sends status. Engagement statuses only move forward. Returns True if changed."""
    current = get_send(send_id)
    if current is None:
        return False
    old = current["status"]
    if status in _STATUS_ORDER and old in _STATUS_ORDER and _STATUS_ORDER[status] <= _STATUS_ORDER[old]:
        return False  # e.g. "opened" after "redeemed" -> keep "redeemed"
    stamp = _iso(now)
    with db() as conn:
        if status == "sent":
            conn.execute("UPDATE offer_sends SET status = ?, sent_at = ?, updated_at = ? WHERE send_id = ?",
                         (status, stamp, stamp, send_id))
        else:
            conn.execute("UPDATE offer_sends SET status = ?, updated_at = ? WHERE send_id = ?",
                         (status, stamp, send_id))
    return True


def active_sends_for_user(user_id: str, now: datetime | None = None, limit: int = 5) -> list[dict]:
    """Offers we sent this user that are still usable (for /offers). Newest first."""
    stamp = _iso(now)
    with db() as conn:
        rows = conn.execute(
            "SELECT s.send_id, s.status, o.* FROM offer_sends s JOIN offers o ON o.offer_id = s.offer_id "
            "WHERE s.user_id = ? AND s.status IN ('sent', 'opened', 'clicked') AND o.is_active = 1 "
            "AND o.start_at <= ? AND o.expires_at >= ? ORDER BY s.sent_at DESC LIMIT ?",
            (user_id, stamp, stamp, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Demo testers (bot-owned table): which synthetic shopper each classmate is playing
# ---------------------------------------------------------------------------

_TESTERS_DDL = ("CREATE TABLE IF NOT EXISTS tester_personas "
                "(user_id TEXT PRIMARY KEY, persona TEXT, trigger_type TEXT, assigned_at TEXT)")


def save_tester(user_id: str, persona: str, trigger_type: str, now: datetime | None = None) -> None:
    with db() as conn:
        conn.execute(_TESTERS_DDL)
        conn.execute("INSERT OR REPLACE INTO tester_personas VALUES (?, ?, ?, ?)",
                     (user_id, persona, trigger_type, _iso(now)))


def list_testers() -> list[dict]:
    """All tester rows, with whether their Telegram link is still active."""
    with db() as conn:
        conn.execute(_TESTERS_DDL)
        rows = conn.execute(
            "SELECT t.*, COALESCE(l.active, 0) AS active FROM tester_personas t "
            "LEFT JOIN telegram_links l ON l.user_id = t.user_id ORDER BY t.assigned_at").fetchall()
    return [dict(r) for r in rows]


def tester_persona(user_id: str) -> dict | None:
    return next((t for t in list_testers() if t["user_id"] == user_id), None)
