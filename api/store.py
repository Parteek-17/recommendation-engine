"""Small, direct SQLite reads/writes the API needs (products, users, events, orders, insights).

Why not use the stubs' data? Stubs read each table ONCE and cache it, but the storefront
must see a click or purchase immediately (e.g. "buy a phone -> covers appear").
So everything here queries app.db fresh on every call.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from common.db import get_connection
from common.logging_utils import now_iso, now_local
from common.schemas import EVENT_WEIGHTS

# Columns the UI needs to draw a product card.
CARD_COLUMNS = ("product_id", "title", "brand", "price", "mrp", "avg_rating", "rating_count",
                "category_id", "stock_qty", "is_consumable", "repurchase_days", "age_restricted")


def _rows(sql: str, params: tuple | list = ()) -> list[dict]:
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _one(sql: str, params: tuple | list = ()) -> dict | None:
    rows = _rows(sql, params)
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def category_names() -> dict[str, str]:
    return {r["category_id"]: r["name"] for r in _rows("SELECT category_id, name FROM categories")}


def list_categories() -> list[dict]:
    return _rows("SELECT category_id, name, parent_id FROM categories ORDER BY category_id")


def get_product(product_id: str) -> dict | None:
    row = _one("SELECT * FROM products WHERE product_id = ?", (product_id,))
    if row:
        row["category_name"] = category_names().get(row["category_id"], row["category_id"])
    return row


def get_products(product_ids: list[str]) -> dict[str, dict]:
    """{product_id: card fields} for many products in one query."""
    if not product_ids:
        return {}
    marks = ",".join("?" for _ in product_ids)
    cols = ", ".join(CARD_COLUMNS)
    rows = _rows(f"SELECT {cols} FROM products WHERE product_id IN ({marks})", list(product_ids))
    names = category_names()
    for r in rows:
        r["category_name"] = names.get(r["category_id"], r["category_id"])
    return {r["product_id"]: r for r in rows}


def search_products(query: str = "", category_id: str | None = None, limit: int = 20) -> list[dict]:
    """Simple text search over title/brand/tags (+ optional category or its sub-categories)."""
    sql = f"SELECT {', '.join(CARD_COLUMNS)} FROM products WHERE 1=1"
    params: list = []
    if query:
        sql += " AND (title LIKE ? OR brand LIKE ? OR tags LIKE ?)"
        params += [f"%{query}%"] * 3
    if category_id:
        sql += " AND (category_id = ? OR category_id IN (SELECT category_id FROM categories WHERE parent_id = ?))"
        params += [category_id, category_id]
    sql += " ORDER BY rating_count DESC LIMIT ?"
    params.append(limit)
    rows = _rows(sql, params)
    names = category_names()
    for r in rows:
        r["category_name"] = names.get(r["category_id"], r["category_id"])
    return rows


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def get_user(user_id: str) -> dict | None:
    return _one("SELECT * FROM users WHERE user_id = ?", (user_id,))


def list_users(limit: int = 200) -> list[dict]:
    """Users with their activity count, most active first (nice for the demo dropdown)."""
    return _rows(
        "SELECT u.user_id, u.city, u.age_group, u.is_minor, COUNT(e.event_id) AS n_events "
        "FROM users u LEFT JOIN events e ON e.user_id = u.user_id "
        "GROUP BY u.user_id ORDER BY u.user_id LIMIT ?",
        (limit,),
    )


def is_minor(user_id: str) -> bool:
    user = get_user(user_id)
    return bool(user and int(user.get("is_minor") or 0) == 1)


def user_events(user_id: str, since: str | None = None) -> list[dict]:
    sql = "SELECT * FROM events WHERE user_id = ?"
    params: list = [user_id]
    if since:
        sql += " AND timestamp >= ?"
        params.append(since)
    return _rows(sql + " ORDER BY timestamp", params)


def recent_purchases(user_id: str, days: int = 30) -> list[dict]:
    """Purchases in the `days` before the user's LATEST event.

    Measured from their last activity (not from today) so the planted cases in the
    historical sample data still count as "recent" in a demo run months later.
    """
    last = _one("SELECT MAX(timestamp) AS t FROM events WHERE user_id = ?", (user_id,))
    if not last or not last["t"]:
        return []
    since = (datetime.fromisoformat(last["t"]) - timedelta(days=days)).isoformat(timespec="seconds")
    return _rows(
        "SELECT e.product_id, MAX(e.timestamp) AS bought_at, p.title, p.category_id, p.is_consumable "
        "FROM events e JOIN products p ON p.product_id = e.product_id "
        "WHERE e.user_id = ? AND e.event_type = 'purchase' AND e.timestamp >= ? "
        "GROUP BY e.product_id ORDER BY bought_at DESC",
        (user_id, since),
    )


def taste_profile(user_id: str, since: str | None = None) -> dict:
    """What the system has learned from clicks: top categories, price band, activity.

    Computed fresh from the events table (weights view 1 … purchase 6).
    `since` = ignore older events (used after "Reset my profile").
    """
    rows = _rows(
        "SELECT e.event_type, p.category_id, p.price FROM events e "
        "JOIN products p ON p.product_id = e.product_id "
        "WHERE e.user_id = ?" + (" AND e.timestamp >= ?" if since else ""),
        (user_id, since) if since else (user_id,),
    )
    scores: dict[str, float] = {}
    prices: list[float] = []
    for r in rows:
        w = EVENT_WEIGHTS.get(r["event_type"], 0)
        if w:
            scores[r["category_id"]] = scores.get(r["category_id"], 0) + w
            prices.append(float(r["price"] or 0))
    names = category_names()
    top = sorted(scores, key=scores.get, reverse=True)[:3]
    prices.sort()
    median = prices[len(prices) // 2] if prices else None
    band = None if median is None else "low" if median < 500 else "mid" if median < 3000 else "high"
    return {
        "top_categories": [{"category_id": c, "name": names.get(c, c), "score": scores[c]} for c in top],
        "budget_band": band,
        "median_price": median,
        "n_signals": len(prices),
    }


def buy_again(user_id: str, within_days: int = 7) -> list[dict]:
    """Consumables the user bought before whose repurchase date is due (or due within a week)."""
    rows = _rows(
        "SELECT e.product_id, MAX(e.timestamp) AS last_bought, p.repurchase_days, p.title "
        "FROM events e JOIN products p ON p.product_id = e.product_id "
        "WHERE e.user_id = ? AND e.event_type = 'purchase' AND p.is_consumable = 1 "
        "AND p.repurchase_days > 0 AND p.stock_qty > 0 GROUP BY e.product_id",
        (user_id,),
    )
    horizon = now_local() + timedelta(days=within_days)
    due = []
    for r in rows:
        last = datetime.fromisoformat(r["last_bought"])
        due_at = last + timedelta(days=int(r["repurchase_days"]))
        if due_at <= horizon:
            r["due_at"] = due_at.isoformat(timespec="seconds")
            r["reason"] = (f"You usually re-buy this every {int(r['repurchase_days'])} days — "
                           f"last bought {last:%d %b}")
            due.append(r)
    return sorted(due, key=lambda r: r["due_at"])


def telegram_status(user_id: str) -> dict:
    link = _one("SELECT active, linked_at FROM telegram_links WHERE user_id = ?", (user_id,))
    consent = _one("SELECT opted_in FROM user_consent WHERE user_id = ?", (user_id,))
    return {
        "linked": bool(link and int(link["active"] or 0) == 1),
        "linked_at": link["linked_at"] if link else None,
        "opted_in": bool(consent and int(consent["opted_in"] or 0) == 1),
    }


# ---------------------------------------------------------------------------
# Writes: sessions, events, orders
# ---------------------------------------------------------------------------


def _next_id(conn: sqlite3.Connection, table: str, column: str, prefix: str, width: int) -> str:
    """Next ID like E00000123. IDs are fixed-width, so MAX() on text gives the biggest number."""
    row = conn.execute(f"SELECT MAX({column}) FROM {table} WHERE {column} LIKE ?", (prefix + "%",)).fetchone()
    last = row[0]
    number = int(last[len(prefix):]) + 1 if last and last[len(prefix):].isdigit() else 1
    return f"{prefix}{number:0{width}d}"


def create_session(user_id: str, device: str = "web", is_gift: bool = False) -> str:
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")  # lock so two requests can't grab the same ID
        session_id = _next_id(conn, "sessions", "session_id", "S", 7)
        conn.execute("INSERT INTO sessions (session_id, user_id, start_time, device, is_gift) VALUES (?, ?, ?, ?, ?)",
                     (session_id, user_id, now_iso(), device, int(is_gift)))
    return session_id


def session_exists(session_id: str) -> bool:
    return _one("SELECT 1 AS x FROM sessions WHERE session_id = ?", (session_id,)) is not None


def log_event(user_id: str, event_type: str, source: str, product_id: str | None = None,
              session_id: str | None = None, search_query: str | None = None) -> dict:
    """Insert one row into `events`. Creates a session if none was given."""
    if not session_id or not session_exists(session_id):
        session_id = create_session(user_id)
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        event_id = _next_id(conn, "events", "event_id", "E", 8)
        conn.execute(
            "INSERT INTO events (event_id, user_id, product_id, session_id, event_type, timestamp, source, search_query) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, user_id, product_id, session_id, event_type, now_iso(), source, search_query),
        )
    return {"event_id": event_id, "session_id": session_id}


def create_order(user_id: str, items: list[tuple[str, int]], session_id: str | None, source: str) -> dict:
    """Checkout: prices come from the DB (never from the browser), one purchase event per item."""
    products = get_products([pid for pid, _ in items])
    if not session_id or not session_exists(session_id):
        session_id = create_session(user_id)
    ts = now_iso()
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        order_id = _next_id(conn, "orders", "order_id", "O", 7)
        total = 0.0
        for pid, qty in items:
            price = float(products[pid]["price"])
            total += price * qty
            conn.execute("INSERT INTO order_items (order_id, product_id, quantity, price_paid) VALUES (?, ?, ?, ?)",
                         (order_id, pid, qty, price))
            event_id = _next_id(conn, "events", "event_id", "E", 8)
            conn.execute(
                "INSERT INTO events (event_id, user_id, product_id, session_id, event_type, timestamp, source, search_query) "
                "VALUES (?, ?, ?, ?, 'purchase', ?, ?, NULL)",
                (event_id, user_id, pid, session_id, ts, source),
            )
        conn.execute("INSERT INTO orders (order_id, user_id, order_date, total_amount) VALUES (?, ?, ?, ?)",
                     (order_id, user_id, ts, round(total, 2)))
    return {"order_id": order_id, "total_amount": round(total, 2), "session_id": session_id}


# ---------------------------------------------------------------------------
# Admin / insights
# ---------------------------------------------------------------------------

# A send that was clicked was also sent and opened, so the funnel counts "at least this far".
_FUNNEL = [("sent", ("sent", "opened", "clicked", "redeemed")),
           ("opened", ("opened", "clicked", "redeemed")),
           ("clicked", ("clicked", "redeemed")),
           ("redeemed", ("redeemed",))]


def offer_funnel() -> dict:
    counts = {r["status"]: r["n"] for r in _rows("SELECT status, COUNT(*) AS n FROM offer_sends GROUP BY status")}
    funnel = [{"stage": stage, "count": sum(counts.get(s, 0) for s in statuses)} for stage, statuses in _FUNNEL]
    by_trigger = _rows(
        "SELECT trigger_type, COUNT(*) AS sends, "
        "SUM(CASE WHEN status = 'redeemed' THEN 1 ELSE 0 END) AS redeemed "
        "FROM offer_sends GROUP BY trigger_type ORDER BY sends DESC"
    )
    return {"funnel": funnel, "blocked": counts.get("blocked", 0), "failed": counts.get("failed", 0),
            "by_trigger": by_trigger}


def offer_sends_log(status: str | None = None, trigger_type: str | None = None,
                    user_id: str | None = None, limit: int = 200) -> list[dict]:
    """One row per offer send (newest first), with the offer's code, for the Admin "Sent offers" table.

    LEFT JOIN so a send still shows even if its offer was later deleted from `offers`.
    """
    where, params = [], []
    for column, value in (("s.status", status), ("s.trigger_type", trigger_type), ("s.user_id", user_id)):
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    sql = ("SELECT s.send_id, s.sent_at, s.user_id, s.offer_id, o.code, s.trigger_type, s.status, "
           "s.channel, s.message, s.updated_at "
           "FROM offer_sends s LEFT JOIN offers o ON o.offer_id = s.offer_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    # Blocked/queued sends never went out, so sent_at is empty; fall back to updated_at for ordering.
    sql += " ORDER BY COALESCE(s.sent_at, s.updated_at) DESC, s.send_id DESC LIMIT ?"
    return _rows(sql, params + [limit])


def guardrail_blocks(limit: int = 50) -> dict:
    """audit_log rows where something was stopped (blocked / refused / fallback)."""
    where = "decision IN ('blocked', 'refused', 'rejected', 'fallback', 'denied')"
    by_reason = _rows(f"SELECT component, reason, COUNT(*) AS n FROM audit_log WHERE {where} "
                      "GROUP BY component, reason ORDER BY n DESC LIMIT 20")
    recent = _rows(f"SELECT timestamp, component, user_id, action, decision, reason FROM audit_log "
                   f"WHERE {where} ORDER BY id DESC LIMIT ?", (limit,))
    return {"by_reason": by_reason, "recent": recent}
