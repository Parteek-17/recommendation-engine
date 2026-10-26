"""Database access for the offers module (reading/writing offers, sends, products).

Every function takes an optional `db_path` so tests can use a throwaway database.
Without it, the project database from `.env` (DB_PATH) is used.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from common.db import get_connection, init_db, read_table
from common.schemas import Offer

# Statuses that mean "this offer really went (or is going) out to the user".
DELIVERED_STATUSES = ("queued", "sent", "opened", "clicked", "redeemed")
# Delivered but not used yet: the user is still holding this offer.
OUTSTANDING_STATUSES = ("queued", "sent", "opened", "clicked")

# Extra table owned by the offers module (not in the Data Contract — see phases/NOTES.md).
# It remembers each product's price when an offer was created, so revalidate() can spot
# a price change between "offer generated" and "message sent".
SNAPSHOT_TABLE = "offer_price_snapshots"


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------


def offer_from_row(row: dict) -> Offer:
    """Turn a DB row into an Offer (empty strings become None)."""
    return Offer(**{k: (None if v == "" else v) for k, v in dict(row).items()})


def load_offers(db_path: str | Path | None = None) -> list[Offer]:
    """Every offer in the DB (active or not)."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        return [offer_from_row(r) for r in conn.execute("SELECT * FROM offers ORDER BY offer_id")]


def get_offer(offer_id: str, db_path: str | Path | None = None) -> Offer | None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM offers WHERE offer_id = ?", (offer_id,)).fetchone()
    return offer_from_row(row) if row else None


def is_live(offer: Offer, now: datetime) -> bool:
    """Active and inside its start/expiry window."""
    return bool(offer.is_active) and offer.start_at <= now <= offer.expires_at


def next_offer_ids(count: int, db_path: str | Path | None = None) -> list[str]:
    """The next `count` free offer IDs: OF0021, OF0022, ..."""
    with get_connection(db_path) as conn:
        ids = [r[0] for r in conn.execute("SELECT offer_id FROM offers")]
    numbers = [int(i[2:]) for i in ids if i.startswith("OF") and i[2:].isdigit()]
    start = max(numbers, default=0) + 1
    return [f"OF{n:04d}" for n in range(start, start + count)]


def existing_codes(db_path: str | Path | None = None) -> set[str]:
    with get_connection(db_path) as conn:
        return {r[0] for r in conn.execute("SELECT code FROM offers")}


def insert_offers(offers: list[Offer], db_path: str | Path | None = None) -> None:
    """Save new offers (dates stored as ISO text, booleans as 0/1)."""
    if not offers:
        return
    rows = []
    for o in offers:
        d = o.model_dump(mode="json")
        d["start_at"], d["expires_at"] = _iso(o.start_at), _iso(o.expires_at)
        d["is_active"], d["requires_approval"] = int(o.is_active), int(o.requires_approval)
        rows.append(d)
    cols = list(rows[0])
    with get_connection(db_path) as conn:
        conn.executemany(
            f"INSERT INTO offers ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})", rows
        )


def set_approval(offer_id: str, approved: bool, db_path: str | Path | None = None) -> bool:
    """Approve (requires_approval -> 0) or reject (is_active -> 0) a pending offer. False if not found."""
    sql = ("UPDATE offers SET requires_approval = 0 WHERE offer_id = ?" if approved
           else "UPDATE offers SET is_active = 0 WHERE offer_id = ?")
    with get_connection(db_path) as conn:
        return conn.execute(sql, (offer_id,)).rowcount > 0


# ---------------------------------------------------------------------------
# Sends (offer history per user)
# ---------------------------------------------------------------------------


def load_sends(user_id: str | None = None, db_path: str | Path | None = None) -> pd.DataFrame:
    """Rows of offer_sends (optionally one user). Adds `when` = sent_at, or updated_at if not sent yet."""
    init_db(db_path)
    sql, params = "SELECT * FROM offer_sends", ()
    if user_id:
        sql, params = sql + " WHERE user_id = ?", (user_id,)
    with get_connection(db_path) as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    df["when"] = df["sent_at"].fillna(df["updated_at"])
    return df


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def get_row(table: str, user_id: str, db_path: str | Path | None = None) -> dict | None:
    """One row of a per-user table (users, user_consent, telegram_links) as a dict."""
    if table not in ("users", "user_consent", "telegram_links"):
        raise ValueError(f"get_row does not support table '{table}'")
    init_db(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(f"SELECT * FROM {table} WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def order_count(user_id: str, db_path: str | Path | None = None) -> int:
    with get_connection(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM orders WHERE user_id = ?", (user_id,)).fetchone()[0]


# ---------------------------------------------------------------------------
# Catalog (products + categories), loaded once per call
# ---------------------------------------------------------------------------


class Catalog:
    """Fast lookups on products and categories: price, margin, stock, category tree."""

    def __init__(self, products: pd.DataFrame, categories: pd.DataFrame):
        self.products = products.set_index("product_id", drop=False) if not products.empty else products
        self.categories = categories
        self._children: dict[str, list[str]] = {}
        for row in categories.itertuples():
            if row.parent_id:
                self._children.setdefault(row.parent_id, []).append(row.category_id)

    @classmethod
    def load(cls, db_path: str | Path | None = None) -> "Catalog":
        init_db(db_path)
        return cls(read_table("products", db_path), read_table("categories", db_path))

    def _get(self, product_id: str, column: str):
        if self.products.empty or product_id not in self.products.index:
            return None
        value = self.products.at[product_id, column]
        return None if pd.isna(value) else value

    def has(self, product_id: str) -> bool:
        return self._get(product_id, "product_id") is not None

    def price(self, product_id: str) -> float | None:
        v = self._get(product_id, "price")
        return float(v) if v is not None else None

    def margin(self, product_id: str) -> float | None:
        v = self._get(product_id, "margin_pct")
        return float(v) if v is not None else None

    def stock(self, product_id: str) -> int:
        return int(self._get(product_id, "stock_qty") or 0)

    def repurchase_days(self, product_id: str) -> int | None:
        v = self._get(product_id, "repurchase_days")
        return int(v) if v else None

    def category_of(self, product_id: str) -> str | None:
        return self._get(product_id, "category_id")

    def is_age_restricted(self, product_id: str) -> bool:
        return int(self._get(product_id, "age_restricted") or 0) == 1

    def is_consumable(self, product_id: str) -> bool:
        return int(self._get(product_id, "is_consumable") or 0) == 1

    def leaf_categories(self, category_id: str) -> set[str]:
        """A parent category means all its children; a leaf means just itself."""
        return set(self._children.get(category_id, [])) or {category_id}

    def products_in(self, category_id: str) -> pd.DataFrame:
        if self.products.empty:
            return self.products
        return self.products[self.products["category_id"].isin(self.leaf_categories(category_id))]

    def median_price(self, category_id: str) -> float | None:
        p = self.products_in(category_id)
        return float(p["price"].median()) if len(p) else None

    def min_margin(self, category_id: str) -> float | None:
        """Lowest margin in the category: a category-wide discount must be safe for every product in it."""
        p = self.products_in(category_id)
        return float(p["margin_pct"].min()) if len(p) else None

    def category_has_age_restricted(self, category_id: str) -> bool:
        p = self.products_in(category_id)
        return bool(len(p)) and bool((p["age_restricted"].fillna(0).astype(int) == 1).any())


# ---------------------------------------------------------------------------
# Price snapshots (for revalidate: "price unchanged")
# ---------------------------------------------------------------------------


def _ensure_snapshot_table(conn) -> None:
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {SNAPSHOT_TABLE} "
        "(offer_id TEXT, product_id TEXT, price REAL, captured_at TEXT, PRIMARY KEY (offer_id, product_id))"
    )


def save_price_snapshots(offers: list[Offer], catalog: Catalog, now: datetime,
                         db_path: str | Path | None = None) -> None:
    """Remember the current price of every product an offer is about."""
    rows = [(o.offer_id, pid, catalog.price(pid), _iso(now))
            for o in offers for pid in (o.product_id, o.bundle_product_id) if pid and catalog.has(pid)]
    with get_connection(db_path) as conn:
        _ensure_snapshot_table(conn)
        conn.executemany(f"INSERT OR REPLACE INTO {SNAPSHOT_TABLE} VALUES (?, ?, ?, ?)", rows)


def get_price_snapshots(offer_id: str, db_path: str | Path | None = None) -> dict[str, float]:
    """{product_id: price when the offer was created} — empty for offers we didn't generate."""
    with get_connection(db_path) as conn:
        _ensure_snapshot_table(conn)
        rows = conn.execute(f"SELECT product_id, price FROM {SNAPSHOT_TABLE} WHERE offer_id = ?", (offer_id,))
        return {r[0]: float(r[1]) for r in rows}
