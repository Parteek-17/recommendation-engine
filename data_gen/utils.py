"""Small helpers shared by the generator steps (random numbers, dates, prices, CSV writing)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from common.db import columns
from data_gen import config as C

# Names of the independent random streams. Each step gets its OWN stream, so changing
# e.g. the review step never changes the catalog. Add new names at the END only.
STREAMS = ["catalog", "relations", "users", "offers", "consent", "simulate", "orders", "reviews"]


def make_rngs(seed: int = C.SEED) -> dict[str, np.random.Generator]:
    """One seeded numpy Generator per step (same seed -> identical data)."""
    children = np.random.SeedSequence(seed).spawn(len(STREAMS))
    return {name: np.random.default_rng(child) for name, child in zip(STREAMS, children)}


def to_date(text: str) -> date:
    return date.fromisoformat(text[:10])


WINDOW_START = to_date(C.WINDOW_START)
WINDOW_END = to_date(C.WINDOW_END)
N_DAYS = (WINDOW_END - WINDOW_START).days + 1


def day_index(d: date | str) -> int:
    """Day number inside the window (0 = WINDOW_START). Negative = before the window."""
    if isinstance(d, str):
        d = to_date(d)
    return (d - WINDOW_START).days


def day_to_date(i: int) -> date:
    return WINDOW_START + timedelta(days=int(i))


def seconds_to_iso(seconds: np.ndarray | pd.Series) -> pd.Series:
    """Seconds since WINDOW_START 00:00 -> ISO text '2026-04-01T19:30:00' (local time, no offset)."""
    base = pd.Timestamp(datetime.combine(WINDOW_START, datetime.min.time()))
    stamps = base + pd.to_timedelta(np.asarray(seconds, dtype="int64"), unit="s")
    return pd.Series(stamps.strftime("%Y-%m-%dT%H:%M:%S"))


def nice_price(x: float) -> int:
    """Round to a store-looking price ending in 9 (1287 -> 1289, 54 -> 59)."""
    x = int(round(x))
    return max(9, x - x % 10 + 9)


def write_tables(tables: dict[str, pd.DataFrame], folder: Path) -> None:
    """Write every Data Contract table as `<table>.csv` with the exact contract columns.

    Tables we have no rows for are still written (header only), so loading the folder
    into the DB always REPLACES every table instead of leaving old rows behind.
    Internal helper columns (names starting with '_') are never written.
    """
    from common.db import TABLES

    folder.mkdir(parents=True, exist_ok=True)
    for table in TABLES:
        df = tables.get(table)
        cols = columns(table)
        if df is None:
            df = pd.DataFrame(columns=cols)
        missing = [c for c in cols if c not in df.columns]
        if missing:
            raise ValueError(f"{table} is missing contract columns: {missing}")
        df[cols].to_csv(folder / f"{table}.csv", index=False, encoding="utf-8")


def write_ground_truth(hidden: pd.DataFrame, relations: pd.DataFrame, folder: Path) -> None:
    """Ground truth goes in its own folder — NEVER next to the model inputs in data/raw/."""
    folder.mkdir(parents=True, exist_ok=True)
    hidden_cols = ["user_id", "preferred_categories", "price_sensitivity", "activity_level", "is_gifter"]
    hidden[hidden_cols].to_csv(folder / "user_hidden_profile.csv", index=False, encoding="utf-8")
    relations[["product_id", "related_product_id", "relation"]].to_csv(
        folder / "product_relations.csv", index=False, encoding="utf-8")
