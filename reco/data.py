"""Step 1 — Data prep: load tables, weight events, build the user×item matrix, split by time.

This is the ONLY reco file that knows where data comes from (app.db, or the CSV folder
as a fallback) and how to survive missing tables/columns. Real external datasets are
often just L1/L2 (events + maybe a catalog), so every table here may be empty.

Words used below:
- "interaction"  = one (user, product) pair with a total weight (view 1 ... purchase 6).
- "matrix"       = a big table, one row per user and one column per product, holding
                   those weights. Mostly zeros, so we store it "sparse" (only non-zeros).
- "time split"   = train on the past, test on the future (never the other way round!).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse

from common.db import (
    FEATURE_LEVELS,
    LEVEL_REQUIREMENTS,
    columns,
    data_folder,
    feature_enabled,
    get_connection,
    get_data_level,
    read_table,
    table_counts,
)
from common.logging_utils import get_logger, now_iso
from common.schemas import DATA_END, DATA_START, EVENT_WEIGHTS, TEST_END, TEST_START, TRAIN_END, VALID_END, VALID_START

log = get_logger(__name__)

RECO_TABLES = ("events", "products", "categories", "users", "sessions", "orders", "order_items")

# Columns that must be numbers (CSV files give us text).
NUMERIC_COLUMNS = {
    "products": ["price", "mrp", "margin_pct", "avg_rating", "rating_count", "stock_qty",
                 "is_consumable", "repurchase_days", "age_restricted"],
    "users": ["city_tier", "is_minor"],
    "sessions": ["is_gift"],
    "orders": ["total_amount"],
    "order_items": ["quantity", "price_paid"],
}

def isin(values, allowed) -> np.ndarray:
    """Fast `values.isin(allowed)` -> boolean numpy array. Use it instead of pandas .isin() in reco.

    Why: pandas 3 stores text as Arrow strings (memory-friendly), but their .isin() is ~100× slower
    (≈90 ms for 200 IDs vs. 3,000) and can abort the whole process with million-row inputs.
    """
    if not isinstance(allowed, (set, frozenset, dict)):
        allowed = set(allowed)
    return np.fromiter((v in allowed for v in values), dtype=bool, count=len(values))


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


@dataclass
class RecoData:
    """All tables the recommender uses, as pandas DataFrames (empty if missing)."""

    events: pd.DataFrame
    products: pd.DataFrame
    categories: pd.DataFrame
    users: pd.DataFrame
    sessions: pd.DataFrame
    orders: pd.DataFrame
    order_items: pd.DataFrame
    level: str = "L0"           # data level L0–L4 (docs/DATA_STANDARD.md)
    origin: str = "db"          # "db" (app.db) or "csv" (fallback)
    db_path: Path | None = None

    @property
    def ref_time(self) -> str:
        """'Today' for this data = the latest event time (our data ends 2026-09-30, not now)."""
        if self.events.empty:
            return now_iso()
        return str(self.events["timestamp"].max())

    def has(self, feature: str) -> bool:
        """Is this feature supported by the data? (no catalog -> no content model, etc.)"""
        if self.origin == "db" and feature != "ground_truth":
            return feature_enabled(feature, self.db_path)
        if feature == "ground_truth":
            return feature_enabled("ground_truth")
        return self.level >= FEATURE_LEVELS[feature]

    def until(self, date: str) -> RecoData:
        """A copy that only contains what was known at the end of `date` (YYYY-MM-DD).

        Used to train on the past and evaluate on the future without "peeking".
        """
        end = date + "T23:59:59"
        events = self.events[self.events["timestamp"].astype(str) <= end]
        orders = self.orders[self.orders["order_date"].astype(str) <= end]
        products = self.products
        if not products.empty:
            launch = products["launch_date"].astype(str)
            # Products launched later didn't exist yet (unknown launch date -> keep; and if events
            # already mention it, the launch date is wrong and the events win).
            products = products[((launch <= date) | products["launch_date"].isna() | (launch == "")).to_numpy()
                                | isin(products["product_id"], set(events["product_id"].dropna()))]
        return replace(
            self,
            events=events,
            sessions=self.sessions[self.sessions["start_time"].astype(str) <= end],
            orders=orders,
            order_items=self.order_items[isin(self.order_items["order_id"], set(orders["order_id"]))],
            products=products,
        )


def _normalize(table: str, df: pd.DataFrame) -> pd.DataFrame:
    """Add missing contract columns (as empty) and fix number types, so code never crashes on them."""
    df = df.copy()
    for col in columns(table):
        if col not in df.columns:
            df[col] = np.nan
    for col in NUMERIC_COLUMNS.get(table, []):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in df.columns:  # IDs and timestamps as text; keep NaN as NaN
        if col.endswith("_id") or col in ("timestamp", "order_date", "start_time", "launch_date", "signup_date"):
            df[col] = df[col].where(df[col].isna(), df[col].astype(str))
    return df


def _level_from_frames(frames: dict[str, pd.DataFrame]) -> str:
    """Same rule as common.db.get_data_level, but for DataFrames (used for the CSV fallback)."""
    level = "L0"
    for name, required in LEVEL_REQUIREMENTS.items():
        if all(t in frames and not frames[t].empty for t in required):
            level = name
        else:
            break
    return level


def load_data(db_path: str | Path | None = None) -> RecoData:
    """Load every table the recommender needs.

    Normal case: read app.db (filled by scripts/init_db.py from DATA_SOURCE).
    Fallback: if app.db has no events, read the CSVs of DATA_SOURCE directly.
    """
    try:
        counts = table_counts(db_path)
    except Exception:  # broken / missing DB
        counts = {}

    frames: dict[str, pd.DataFrame] = {}
    if counts.get("events", 0) > 0:
        origin = "db"
        for t in RECO_TABLES:
            frames[t] = read_table(t, db_path) if counts.get(t, 0) > 0 else pd.DataFrame(columns=columns(t))
        level = get_data_level(db_path)
    else:
        origin = "csv"
        folder = data_folder()
        log.warning("app.db has no events — reading CSVs from %s (run scripts/init_db.py to fix).", folder)
        for t in RECO_TABLES:
            path = folder / f"{t}.csv"
            frames[t] = pd.read_csv(path, dtype=str) if path.exists() else pd.DataFrame(columns=columns(t))
        level = _level_from_frames(frames)

    frames = {t: _normalize(t, df) for t, df in frames.items()}
    return RecoData(**frames, level=level, origin=origin, db_path=Path(db_path) if db_path else None)


# ---------------------------------------------------------------------------
# Time split
# ---------------------------------------------------------------------------


@dataclass
class Split:
    """Inclusive date ranges (YYYY-MM-DD) for train / validation / test."""

    train_end: str
    valid_start: str
    valid_end: str
    test_start: str
    test_end: str


def make_split(events: pd.DataFrame) -> Split:
    """Use the contract dates (CLAUDE.md) when the data lies in our window; otherwise split
    by time proportion (last ~16% of days = validation, last ~8% = test) so external data works too."""
    days = events["timestamp"].astype(str).str[:10]
    inside = ((days >= DATA_START) & (days <= DATA_END)).mean() if len(days) else 0
    if inside >= 0.5:
        return Split(TRAIN_END, VALID_START, VALID_END, TEST_START, TEST_END)

    start, end = pd.to_datetime(days.min()), pd.to_datetime(days.max())
    span = max((end - start).days, 3)
    valid_start = end - pd.Timedelta(days=round(span * 0.16))
    test_start = end - pd.Timedelta(days=round(span * 0.08))
    fmt = "%Y-%m-%d"
    return Split(
        train_end=(valid_start - pd.Timedelta(days=1)).strftime(fmt),
        valid_start=valid_start.strftime(fmt),
        valid_end=(test_start - pd.Timedelta(days=1)).strftime(fmt),
        test_start=test_start.strftime(fmt),
        test_end=end.strftime(fmt),
    )


def shift_date(date: str, days: int) -> str:
    """'2026-09-01' + 3 days -> '2026-09-04'."""
    return (pd.to_datetime(date) + pd.Timedelta(days=days)).strftime("%Y-%m-%d")


def window(events: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """Events whose date is between start and end (inclusive)."""
    days = events["timestamp"].astype(str).str[:10]
    return events[(days >= start) & (days <= end)]


def relevance_labels(events: pd.DataFrame) -> pd.DataFrame:
    """(user_id, product_id, grade): grade = strongest event weight the user had on the item.

    This is the "answer key" for evaluation: what did the user actually engage with?
    """
    ev = events[events["event_type"].isin(EVENT_WEIGHTS) & events["product_id"].notna()]
    grade = ev["event_type"].map(EVENT_WEIGHTS)
    return (ev.assign(grade=grade).groupby(["user_id", "product_id"], as_index=False)["grade"].max())


# ---------------------------------------------------------------------------
# Weighted interactions + matrix
# ---------------------------------------------------------------------------


def gift_session_ids(sessions: pd.DataFrame) -> set[str]:
    """Sessions marked as gift shopping. Their events say nothing about the user's OWN taste."""
    if sessions.empty:
        return set()
    return set(sessions.loc[sessions["is_gift"].fillna(0).astype(int) == 1, "session_id"])


def weighted_interactions(events: pd.DataFrame, sessions: pd.DataFrame | None = None,
                          personal: bool = True, gift_ids: set[str] | None = None) -> pd.DataFrame:
    """Sum event weights per (user, product).

    personal=True drops gift-session events (gift mode must not change the user's profile).
    gift_ids: pre-computed gift session IDs (saves re-scanning `sessions` for every user).
    Returns columns: user_id, product_id, weight, last_ts.
    """
    ev = events[events["event_type"].isin(EVENT_WEIGHTS) & events["product_id"].notna()]
    if personal and (sessions is not None or gift_ids is not None):
        gifts = gift_ids if gift_ids is not None else gift_session_ids(sessions)
        if gifts:
            ev = ev[~isin(ev["session_id"], gifts)]
    ev = ev.assign(weight=ev["event_type"].map(EVENT_WEIGHTS).astype(float))
    return ev.groupby(["user_id", "product_id"], as_index=False).agg(weight=("weight", "sum"),
                                                                    last_ts=("timestamp", "max"))


@dataclass
class InteractionMatrix:
    """users × items matrix of weights + the lookups between IDs and row/column numbers."""

    matrix: sparse.csr_matrix
    user_ids: list[str]
    item_ids: list[str]
    user_index: dict[str, int] = field(default_factory=dict)
    item_index: dict[str, int] = field(default_factory=dict)


def build_matrix(interactions: pd.DataFrame, item_ids: list[str]) -> InteractionMatrix:
    """Turn the (user, product, weight) list into a sparse matrix.

    item_ids fixes the column order (usually the whole catalog, so every model agrees
    that column 17 is the same product).
    """
    item_index = {pid: i for i, pid in enumerate(item_ids)}
    inter = interactions[isin(interactions["product_id"], item_index)]
    user_ids = sorted(inter["user_id"].unique())
    user_index = {uid: i for i, uid in enumerate(user_ids)}
    rows = inter["user_id"].map(user_index).to_numpy()
    cols = inter["product_id"].map(item_index).to_numpy()
    mat = sparse.csr_matrix((inter["weight"].to_numpy(dtype=np.float32), (rows, cols)),
                            shape=(len(user_ids), len(item_ids)))
    return InteractionMatrix(mat, user_ids, list(item_ids), user_index, item_index)


# ---------------------------------------------------------------------------
# One user's history (used at request time and in evaluation)
# ---------------------------------------------------------------------------


@dataclass
class UserHistory:
    """Everything we know about one user, at one moment in time."""

    user_id: str
    weights: dict[str, float] = field(default_factory=dict)     # personal taste: product -> weight
    last_seen: dict[str, str] = field(default_factory=dict)     # product -> last event time (any)
    purchases: dict[str, str] = field(default_factory=dict)     # product -> last purchase time
    cart: set[str] = field(default_factory=set)                 # still in cart (added, not removed/bought)
    wishlist: set[str] = field(default_factory=set)
    now: str = ""                                               # time of the user's latest event
    profile: dict = field(default_factory=dict)                 # row of the users table (or {})

    @property
    def is_cold(self) -> bool:
        """No personal history at all -> cold-start user."""
        return not self.weights


def history_from_frames(user_id: str, events: pd.DataFrame, sessions: pd.DataFrame,
                        orders: pd.DataFrame, order_items: pd.DataFrame,
                        users: pd.DataFrame, gift_ids: set[str] | None = None) -> UserHistory:
    """Build a UserHistory from DataFrames that contain (at least) this user's rows."""
    ev = events[events["user_id"] == user_id] if not events.empty else events
    hist = UserHistory(user_id=user_id)
    if not ev.empty:
        hist.now = str(ev["timestamp"].max())
        inter = weighted_interactions(ev, sessions, personal=True, gift_ids=gift_ids)
        hist.weights = dict(zip(inter["product_id"], inter["weight"]))
        with_product = ev[ev["product_id"].notna()]
        hist.last_seen = with_product.groupby("product_id")["timestamp"].max().to_dict()

        buys = with_product[with_product["event_type"] == "purchase"]
        hist.purchases = buys.groupby("product_id")["timestamp"].max().to_dict()

        # Cart state: replay add/remove/purchase in time order.
        for row in with_product.sort_values("timestamp").itertuples():
            if row.event_type == "add_to_cart":
                hist.cart.add(row.product_id)
            elif row.event_type in ("remove_from_cart", "purchase"):
                hist.cart.discard(row.product_id)
            elif row.event_type == "wishlist":
                hist.wishlist.add(row.product_id)
        hist.wishlist -= set(hist.purchases)

    # Orders table adds purchases that may not be in events (e.g. imported order history).
    if not orders.empty and not order_items.empty:
        mine = orders[orders["user_id"] == user_id]
        if not mine.empty:
            items = order_items.merge(mine[["order_id", "order_date"]], on="order_id")
            for pid, ts in items.groupby("product_id")["order_date"].max().items():
                hist.purchases[pid] = max(ts, hist.purchases.get(pid, ""))
            hist.now = max(hist.now, str(mine["order_date"].max()))

    if not users.empty:
        row = users[users["user_id"] == user_id]
        if not row.empty:
            hist.profile = {k: v for k, v in row.iloc[0].to_dict().items() if pd.notna(v)}
    return hist


def all_histories(data: RecoData, user_ids: list[str] | None = None) -> dict[str, UserHistory]:
    """UserHistory for many users at once (groups the tables once instead of filtering per user)."""
    wanted = set(user_ids) if user_ids is not None else set(data.events["user_id"].dropna())
    # Group every table by user ONCE (filtering 1M rows per user would take hours on full data).
    events = data.events[isin(data.events["user_id"], wanted)]
    ev_groups = dict(tuple(events.groupby("user_id")))
    orders = data.orders[isin(data.orders["user_id"], wanted)]
    ord_groups = dict(tuple(orders.groupby("user_id")))
    items = data.order_items.merge(orders[["order_id", "user_id"]], on="order_id")
    item_groups = dict(tuple(items.groupby("user_id")))
    users = data.users[isin(data.users["user_id"], wanted)]
    user_groups = dict(tuple(users.groupby("user_id")))
    gift_ids = gift_session_ids(data.sessions)
    empty_ev, empty_ord, empty_items = data.events.iloc[0:0], data.orders.iloc[0:0], data.order_items.iloc[0:0]
    return {
        uid: history_from_frames(uid, ev_groups.get(uid, empty_ev), data.sessions, ord_groups.get(uid, empty_ord),
                                 item_groups.get(uid, empty_items), user_groups.get(uid, data.users.iloc[0:0]),
                                 gift_ids=gift_ids)
        for uid in wanted
    }


def load_user_history(user_id: str, db_path: str | Path | None = None) -> UserHistory:
    """Read ONE user's latest history straight from app.db (fresh: includes events logged a second ago).

    Falls back to the CSV folder if the DB has no events.
    """
    try:
        if table_counts(db_path).get("events", 0) > 0:
            with get_connection(db_path) as conn:
                q = lambda sql: pd.read_sql_query(sql, conn, params=(user_id,))  # noqa: E731
                frames = {
                    "events": q("SELECT * FROM events WHERE user_id = ?"),
                    "sessions": q("SELECT * FROM sessions WHERE user_id = ?"),
                    "orders": q("SELECT * FROM orders WHERE user_id = ?"),
                    "order_items": q("SELECT oi.* FROM order_items oi JOIN orders o ON o.order_id = oi.order_id "
                                     "WHERE o.user_id = ?"),
                    "users": q("SELECT * FROM users WHERE user_id = ?"),
                }
            frames = {t: _normalize(t, df) for t, df in frames.items()}
            return history_from_frames(user_id, **frames)
    except Exception as exc:  # DB problem -> still answer from CSVs
        log.warning("Could not read history from app.db (%s); using CSV data.", exc)
    data = _csv_data()
    return history_from_frames(user_id, data.events, data.sessions, data.orders, data.order_items, data.users)


@lru_cache(maxsize=1)
def _csv_data() -> RecoData:
    return load_data()
