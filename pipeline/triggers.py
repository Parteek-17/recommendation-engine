"""Trigger detection: which users have a reason to get an offer right now? (CLI: python -m pipeline.show_triggers)

    from pipeline import detect_triggers
    detect_triggers(now)   ->  [("U00004", "cart_abandon"), ("U00002", "repurchase_due"), ...]

One small function per trigger type. Each reads only the data it needs (SQL with a time
window, not the whole 1M-row events table) and only looks at events up to `now`, so you
can also ask "what would have triggered on 2026-09-28 at 18:00?".

| Trigger         | Fires when                                                                      |
|-----------------|---------------------------------------------------------------------------------|
| cart_abandon    | something was added to the cart > 2 h ago (within 3 days), not bought / removed |
| price_drop      | a wishlisted item (last 60 days, not bought) is now cheaper than when wishlisted |
| repurchase_due  | a consumable's last purchase + repurchase_days is between 7 days ago and 2 days ahead |
| post_purchase   | a purchase in the last 3 days of a product that has known complements           |
| win_back        | a past buyer has been inactive for 30–90 days                                   |
| festival        | `now` is inside a festival window (FESTIVALS) → every opted-in user             |

The result is sorted by trigger PRIORITY (cart_abandon first): with a daily cap of 1 offer,
the most urgent reason should be the one that gets used.

Detection only says "there is a reason". Whether we may actually message the user
(consent, Telegram link, quiet hours, caps...) is decided later by the offer pipeline.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from common.db import get_connection
from common.logging_utils import get_logger, now_local
from common.schemas import TriggerType
from pipeline import store

try:
    from reco import get_complements
except ImportError:
    from common.stubs import get_complements

log = get_logger(__name__)

# --- Tuning knobs (see the table above) ---------------------------------------
CART_IDLE_HOURS = 2
CART_LOOKBACK_DAYS = 3
WISHLIST_LOOKBACK_DAYS = 60
PRICE_DROP_MIN_PCT = 1.0          # ignore "drops" of a few paise
REPURCHASE_EARLY_DAYS = 2         # remind up to 2 days before it runs out...
REPURCHASE_LATE_DAYS = 7          # ...and up to a week after
POST_PURCHASE_DAYS = 3
WIN_BACK_DAYS = 30
WIN_BACK_MAX_DAYS = 90            # longer than this = churned, a coupon won't help

# Festival / sale windows (local dates, inclusive). Indian marketplace calendar.
FESTIVALS: list[tuple[str, str, str]] = [
    ("Independence Day Sale", "2026-08-10", "2026-08-15"),
    ("Diwali", "2026-10-20", "2026-11-15"),
    ("Christmas & New Year", "2026-12-23", "2027-01-01"),
    ("Republic Day Sale", "2027-01-23", "2027-01-26"),
]

# Most urgent first. A user with several reasons is tried in this order.
PRIORITY: list[str] = [
    TriggerType.CART_ABANDON.value,
    TriggerType.PRICE_DROP.value,
    TriggerType.POST_PURCHASE.value,
    TriggerType.REPURCHASE_DUE.value,
    TriggerType.WIN_BACK.value,
    TriggerType.FESTIVAL.value,
]


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _events(db_path, since: datetime, until: datetime, types: tuple[str, ...]) -> pd.DataFrame:
    """Events of the given types in [since, until]."""
    marks = ", ".join("?" for _ in types)
    with get_connection(db_path) as conn:
        return pd.read_sql_query(
            f"SELECT user_id, product_id, event_type, timestamp FROM events "
            f"WHERE timestamp >= ? AND timestamp <= ? AND event_type IN ({marks})",
            conn, params=(_iso(since), _iso(until), *types),
        )


# ---------------------------------------------------------------------------
# One detector per trigger type. Each returns a set of user_ids.
# ---------------------------------------------------------------------------


def cart_abandon_users(now: datetime, db_path=None) -> set[str]:
    ev = _events(db_path, now - timedelta(days=CART_LOOKBACK_DAYS), now,
                 ("add_to_cart", "remove_from_cart", "purchase"))
    if ev.empty:
        return set()
    # One row per (user, product), one column per event type = latest time it happened.
    last = ev.groupby(["user_id", "product_id", "event_type"])["timestamp"].max().unstack("event_type")
    last = last.fillna("")  # "" sorts before every timestamp = "never happened"

    def col(name: str) -> pd.Series:
        return last[name] if name in last.columns else pd.Series("", index=last.index)

    added = col("add_to_cart")
    closed = pd.concat([col("purchase"), col("remove_from_cart")], axis=1).max(axis=1)
    still_open = (added != "") & (closed < added)
    # The cart must be idle: the user's LAST add-to-cart is older than CART_IDLE_HOURS
    # (if they added something 10 minutes ago, they are still shopping).
    last_add = added[added != ""].groupby(level="user_id").max()
    idle_before = _iso(now - timedelta(hours=CART_IDLE_HOURS))
    open_users = set(added[still_open].index.get_level_values("user_id"))
    return {u for u in open_users if last_add[u] <= idle_before}


def price_drop_users(now: datetime, db_path=None) -> set[str]:
    """Needs the pipeline's price_history (the scheduler snapshots prices every run)."""
    history = store.price_history(db_path)
    if not history:
        return set()
    ev = _events(db_path, now - timedelta(days=WISHLIST_LOOKBACK_DAYS), now, ("wishlist", "purchase"))
    wished = ev[ev["event_type"] == "wishlist"].groupby(["user_id", "product_id"])["timestamp"].max()
    bought = ev[ev["event_type"] == "purchase"].groupby(["user_id", "product_id"])["timestamp"].max()
    with get_connection(db_path) as conn:
        prices = {r["product_id"]: r["price"] for r in conn.execute("SELECT product_id, price FROM products")}

    users = set()
    for (user_id, product_id), wished_at in wished.items():
        if (user_id, product_id) in bought.index and bought[(user_id, product_id)] >= wished_at:
            continue
        snaps = history.get(product_id)
        current = prices.get(product_id)
        if not snaps or current is None:
            continue
        # Price the user saw when wishlisting = last snapshot at/before that moment
        # (or our oldest snapshot, if we started tracking after they wishlisted).
        before = [p for ts, p in snaps if ts <= wished_at]
        reference = before[-1] if before else snaps[0][1]
        if current < reference * (1 - PRICE_DROP_MIN_PCT / 100):
            users.add(user_id)
    return users


def repurchase_due_users(now: datetime, db_path=None) -> set[str]:
    with get_connection(db_path) as conn:
        df = pd.read_sql_query(
            "SELECT e.user_id, e.product_id, MAX(e.timestamp) AS last_buy, p.repurchase_days "
            "FROM events e JOIN products p ON p.product_id = e.product_id "
            "WHERE e.event_type = 'purchase' AND p.is_consumable = 1 AND p.repurchase_days > 0 AND e.timestamp <= ? "
            "GROUP BY e.user_id, e.product_id",
            conn, params=(_iso(now),),
        )
    if df.empty:
        return set()
    due = pd.to_datetime(df["last_buy"]) + pd.to_timedelta(df["repurchase_days"].astype(float), unit="D")
    window = (due >= now - timedelta(days=REPURCHASE_LATE_DAYS)) & (due <= now + timedelta(days=REPURCHASE_EARLY_DAYS))
    return set(df.loc[window, "user_id"])


def post_purchase_users(now: datetime, db_path=None) -> set[str]:
    ev = _events(db_path, now - timedelta(days=POST_PURCHASE_DAYS), now, ("purchase",))
    has_complements: dict[str, bool] = {}
    users = set()
    for user_id, product_id in zip(ev["user_id"], ev["product_id"]):
        if product_id not in has_complements:
            try:
                has_complements[product_id] = bool(get_complements(product_id, k=3))
            except Exception as exc:  # reco problem: skip this product, keep detecting the rest
                log.warning("get_complements(%s) failed: %s", product_id, exc)
                has_complements[product_id] = False
        if has_complements[product_id]:
            users.add(user_id)
    return users


def win_back_users(now: datetime, db_path=None) -> set[str]:
    with get_connection(db_path) as conn:
        df = pd.read_sql_query(
            "SELECT user_id, MAX(timestamp) AS last_seen, SUM(event_type = 'purchase') AS purchases "
            "FROM events WHERE timestamp <= ? GROUP BY user_id",
            conn, params=(_iso(now),),
        )
    quiet = (df["last_seen"] <= _iso(now - timedelta(days=WIN_BACK_DAYS))) & \
            (df["last_seen"] >= _iso(now - timedelta(days=WIN_BACK_MAX_DAYS))) & (df["purchases"] > 0)
    return set(df.loc[quiet, "user_id"])


def active_festival(now: datetime) -> str | None:
    """Name of the festival window `now` falls in, else None."""
    day = now.date().isoformat()
    return next((name for name, start, end in FESTIVALS if start <= day <= end), None)


def festival_users(now: datetime, db_path=None) -> set[str]:
    """Festival offers are for everyone, but only opted-in users can receive them, so we
    don't create thousands of pipeline runs that can only end in 'not opted in'."""
    if active_festival(now) is None:
        return set()
    with get_connection(db_path) as conn:
        return {r["user_id"] for r in conn.execute("SELECT user_id FROM user_consent WHERE opted_in = 1")}


DETECTORS = {
    TriggerType.CART_ABANDON.value: cart_abandon_users,
    TriggerType.PRICE_DROP.value: price_drop_users,
    TriggerType.POST_PURCHASE.value: post_purchase_users,
    TriggerType.REPURCHASE_DUE.value: repurchase_due_users,
    TriggerType.WIN_BACK.value: win_back_users,
    TriggerType.FESTIVAL.value: festival_users,
}


def detect_triggers(now: datetime | None = None, *, db_path: str | Path | None = None) -> list[tuple[str, str]]:
    """Module-contract function: all (user_id, trigger_type) pairs at `now`, most urgent first."""
    now = now or now_local()
    found: list[tuple[str, str]] = []
    for trigger in PRIORITY:
        try:
            users = DETECTORS[trigger](now, db_path)
        except Exception as exc:  # one broken detector must not hide the other five
            log.error("Trigger detector %s failed: %s", trigger, exc)
            users = set()
        found += [(u, trigger) for u in sorted(users)]
    return found
