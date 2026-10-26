"""Read-only access to catalog facts (products, categories, users, offers) for the LLM layer.

Why a separate file: every fact the user sees (price, rating, stock, offer code) must come
from HERE — the database — never from Claude. Guardrails also use these lookups to check
that what Claude wrote matches reality.

Data comes from app.db; if the DB is missing/empty we fall back to data/sample/*.csv so the
module works on a fresh checkout.
"""

from __future__ import annotations

from functools import lru_cache

import pandas as pd

from common.db import data_folder, read_table, table_counts


@lru_cache(maxsize=None)
def _table(name: str) -> pd.DataFrame:
    """Whole table as a DataFrame (cached once per process)."""
    try:
        if table_counts().get(name, -1) > 0:
            return read_table(name)
    except Exception:  # no DB yet -> sample CSVs
        pass
    path = data_folder("sample") / f"{name}.csv"
    return pd.read_csv(path, keep_default_na=False) if path.exists() else pd.DataFrame()


def clear_cache() -> None:
    """Forget cached tables (call after the DB was reloaded)."""
    _table.cache_clear()
    _products_by_id.cache_clear()


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------


def products() -> pd.DataFrame:
    return _table("products")


@lru_cache(maxsize=None)
def _products_by_id() -> dict[str, dict]:
    return {str(r["product_id"]): r for r in products().to_dict("records")}


def get_product(product_id: str) -> dict | None:
    """One product row as a dict, or None if the ID is not in the catalog."""
    return _products_by_id().get(str(product_id))


def product_exists(product_id: str) -> bool:
    return get_product(product_id) is not None


def is_age_restricted(product_id: str) -> bool:
    p = get_product(product_id)
    return bool(p) and int(p.get("age_restricted") or 0) == 1


def category_name(category_id: str | None) -> str:
    """'C010' -> 'Mobile Accessories' (or the ID itself if unknown)."""
    if not category_id:
        return ""
    cats = _table("categories")
    row = cats[cats["category_id"] == category_id]
    return str(row.iloc[0]["name"]) if len(row) else str(category_id)


def category_ids_matching(text: str) -> list[str]:
    """Category IDs whose name appears in `text` (e.g. 'footwear' -> ['C012'])."""
    text = text.lower()
    cats = _table("categories")
    return [r.category_id for r in cats.itertuples() if str(r.name).lower() in text]


def child_categories(category_id: str) -> list[str]:
    cats = _table("categories")
    return cats[cats["parent_id"] == category_id]["category_id"].tolist()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def user_exists(user_id: str) -> bool:
    users = _table("users")
    return bool(len(users)) and bool((users["user_id"] == user_id).any())


def is_minor(user_id: str | None) -> bool:
    if not user_id:
        return False
    users = _table("users")
    row = users[users["user_id"] == user_id]
    return bool(len(row)) and int(row.iloc[0]["is_minor"] or 0) == 1


# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------


def all_offer_codes() -> set[str]:
    offers = _table("offers")
    return set(offers["code"].astype(str).str.upper()) if len(offers) else set()


def all_offer_values() -> set[float]:
    offers = _table("offers")
    return set(offers["value"].astype(float)) if len(offers) else set()


# ---------------------------------------------------------------------------
# Formatting (so every price shown to users looks the same)
# ---------------------------------------------------------------------------


def format_inr(amount: float) -> str:
    """15999 -> '₹15,999'; 123456 -> '₹1,23,456' (Indian digit grouping)."""
    amount = round(float(amount))
    sign, n = ("-" if amount < 0 else ""), str(abs(int(amount)))
    if len(n) > 3:
        head, tail = n[:-3], n[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        n = ",".join(groups) + "," + tail
    return f"{sign}₹{n}"


def product_card(product_id: str) -> str:
    """One line describing a product, built ONLY from DB values."""
    p = get_product(product_id)
    if p is None:
        return ""
    stock = "in stock" if float(p.get("stock_qty") or 0) > 0 else "out of stock"
    return f"{p['title']} — {format_inr(p['price'])} · ★{float(p['avg_rating']):.1f} · {stock}"
