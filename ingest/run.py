"""Convert a real dataset into the Standard Format, validate it and load it into the DB.

    python -m ingest.run --mapping ingest/mappings/retailrocket.yaml
    python -m ingest.run --mapping ingest/mappings/amazon_reviews_2023.yaml --sample 5000
    python -m ingest.run --mapping ingest/mappings/myntra_fashion.yaml --fill-missing
    python -m ingest.run --mapping ingest/mappings/retailrocket.yaml --input tests/ingest/fixtures/retailrocket --db scratch.db

What happens (docs/DATA_STANDARD.md):
  1. read each raw file (csv / jsonl / json, in chunks) and rename columns as the mapping says
  2. apply transforms (dates, money, ratings, event names, category paths, HTML)
  3. drop bad rows (no user / product / time, unknown event type, duplicates) and COUNT why
  4. map external ids to ours (U00001, P00001, ...) and remember them in id_map
  5. build what's missing: categories from paths, sessions (30-min gap), users, orders,
     purchase events from reviews; fill product defaults ("If missing" column of the spec)
  6. --fill-missing: offers + consent (and demographics, or whole shoppers for a catalog-only
     dataset) from our generator
  7. write data/processed/<source>/*.csv + report.md, validate, load into the DB

Personal data is never copied: only mapped Standard fields are kept, and ids that look like
emails or phone numbers are stored hashed in id_map.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import pandas as pd
import yaml

from common.config import PROJECT_ROOT, settings
from common.db import TABLES, columns, load_csvs_to_db
from common.schemas import EventSource, EventType
from data_gen import config as C
from data_gen.utils import write_tables
from data_gen.validate import format_report, validate_folder
from ingest.transforms import PATH_SEP, TRANSFORMS, strip_html

PROCESSED = PROJECT_ROOT / "data" / "processed"
HELPER_FIELDS = {"products": {"category_path"}, "events": {"rating"}}
PII_HINT = re.compile(r"(user|customer|first|last|full|buyer|reviewer|seller|profile)_?name|e-?mail|phone|"
                      r"mobile_?(no|num)|address|street|zip|pin_?code|^ip$|ip_?addr|card_?(no|num)|aadhaar",
                      re.IGNORECASE)
ENTITY_PREFIX = {"user": ("U", 5), "product": ("P", 5), "session": ("S", 7), "order": ("O", 7)}
# Parent-category keywords -> our parent names (for margin and consumable defaults).
PARENT_KEYWORDS = [
    ("Electronics", r"electronic|mobile|phone|computer|laptop|camera|audio|tv|gadget|cell"),
    ("Fashion", r"fashion|cloth|apparel|shoe|footwear|jewel|watch|bag|wear|men|women"),
    ("Home & Kitchen", r"home|kitchen|furniture|appliance|decor|garden|tools"),
    ("Beauty", r"beauty|personal care|cosmetic|skin|hair|makeup|fragrance"),
    ("Grocery", r"grocery|food|gourmet|beverage|snack|pantry"),
    ("Sports", r"sport|fitness|outdoor|gym|cycling"),
    ("Books", r"book|kindle|magazine"),
    ("Toys", r"toy|game|baby|kids"),
]
CONSUMABLE_DAYS = {"Grocery": 30, "Beauty": 45}
# Time fields are always normalised to ISO local time, even when the mapping names no transform.
TIME_FIELDS = {"timestamp", "review_date", "order_date", "signup_date", "launch_date", "start_time", "start_at",
               "expires_at", "sent_at", "updated_at", "created_at", "linked_at"}


@dataclass
class Tracker:
    """Everything the report needs: rows in/out, why rows were dropped, what was estimated."""

    rows_in: Counter = field(default_factory=Counter)
    drops: dict[str, Counter] = field(default_factory=dict)
    estimated: Counter = field(default_factory=Counter)
    built: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    pii_columns: set[str] = field(default_factory=set)

    def drop(self, table: str, reason: str, n: int) -> None:
        if n:
            self.drops.setdefault(table, Counter())[reason] += int(n)


@dataclass
class RunResult:
    out_dir: Path
    tables: dict[str, pd.DataFrame]
    tracker: Tracker
    report: Any


# ============================================================================ reading
def load_mapping(path: str | Path) -> dict:
    path = Path(path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict) or "source" not in cfg or not cfg.get("files"):
        raise ValueError(f"{path}: a mapping needs 'source' and at least one entry under 'files'")
    for table, spec in cfg["files"].items():
        if spec.get("map") == "same":   # shorthand: raw columns already have the Standard names
            spec["map"] = {c: c for c in columns(table)} if table in TABLES else {}
        if table not in TABLES:
            raise ValueError(f"{path}: unknown table '{table}'. Use Standard Format names: {', '.join(TABLES)}")
        allowed = set(columns(table)) | HELPER_FIELDS.get(table, set())
        unknown = set(spec.get("map", {})) - allowed
        if unknown:
            raise ValueError(f"{path}: files.{table}.map has unknown fields {sorted(unknown)}; allowed: {sorted(allowed)}")
    return cfg


def read_raw(path: Path, fmt: str, chunksize: int) -> Iterator[pd.DataFrame]:
    """Yield the raw file in chunks so huge files never have to fit in memory at once."""
    if fmt == "csv":
        yield from pd.read_csv(path, dtype=str, keep_default_na=False, chunksize=chunksize)
    elif fmt == "jsonl":
        yield from pd.read_json(path, lines=True, chunksize=chunksize, dtype=False)
    elif fmt == "json":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):   # {"items": [...]} -> the first list inside
            data = next((v for v in data.values() if isinstance(v, list)), [data])
        for start in range(0, len(data), chunksize):
            yield pd.DataFrame(data[start:start + chunksize])
    else:
        raise ValueError(f"Unknown format '{fmt}' (use csv, jsonl or json)")


def apply_map(chunk: pd.DataFrame, field_map: dict, ctx: dict, table: str, tracker: Tracker) -> pd.DataFrame:
    """Raw chunk -> DataFrame with Standard field names (only mapped fields are kept)."""
    out: dict[str, Any] = {}
    for std, spec in field_map.items():
        spec = {"field": spec} if isinstance(spec, str) else dict(spec)
        if "const" in spec:
            out[std] = spec["const"]
            continue
        raw = spec.pop("field")
        if raw not in chunk.columns:
            note = f"{table}.{std}: raw column '{raw}' not found in the file -> treated as missing"
            if note not in tracker.notes:
                tracker.notes.append(note)
            continue
        col = chunk[raw]
        name = spec.pop("transform", None)
        if name is None and std in TIME_FIELDS and not ctx.get("keep_ids"):
            name = "date"
        if name:
            if name not in TRANSFORMS:
                raise ValueError(f"Unknown transform '{name}'. Known: {', '.join(TRANSFORMS)}")
            if name == "money":
                spec.setdefault("rate", ctx["rate"])
            if name == "event_map":
                spec.setdefault("mapping", spec.pop("map", None) or ctx["event_map"])
            col = TRANSFORMS[name](col, **spec)
        out[std] = col
    return pd.DataFrame(out, index=chunk.index)


def read_table(table: str, spec: dict, cfg: dict, input_dir: Path | None, chunksize: int,
               tracker: Tracker) -> pd.DataFrame:
    path = Path(spec["path"])
    if input_dir is not None:
        path = input_dir / path.name
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        raise FileNotFoundError(f"{table}: raw file not found: {path} (put it there, or use --input DIR)")
    ctx = {"rate": _rate(cfg), "event_map": spec.get("event_map") or cfg.get("event_map") or {},
           "keep_ids": bool(cfg.get("keep_ids", False))}
    parts = []
    for chunk in read_raw(path, spec.get("format", path.suffix.lstrip(".")), chunksize):
        tracker.rows_in[table] += len(chunk)
        mapped = {v if isinstance(v, str) else v.get("field") for v in spec["map"].values()}
        tracker.pii_columns |= {c for c in chunk.columns if PII_HINT.search(str(c)) and c not in mapped}
        parts.append(apply_map(chunk, spec["map"], ctx, table, tracker))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=list(spec["map"]))


def _rate(cfg: dict) -> float:
    if "rate" in cfg:
        return float(cfg["rate"])
    currency = str(cfg.get("currency", "INR")).upper()
    if currency == "INR":
        return 1.0
    if currency == "USD":
        return float(cfg.get("usd_to_inr", 84))
    raise ValueError(f"currency {currency}: add 'rate: <₹ per 1 {currency}>' to the mapping file")


# ============================================================================ helpers
def _text(s: pd.Series) -> pd.Series:
    """Any column -> clean strings, missing = ''."""
    return s.astype("string").fillna("").str.strip().astype(object)


def _col(df: pd.DataFrame, name: str, default: Any = "") -> pd.Series:
    return df[name] if name in df.columns else pd.Series([default] * len(df), index=df.index, dtype=object)


def _safe_external_id(value: str) -> str:
    """Never keep personal data in id_map: emails / phone numbers are stored as a hash."""
    if "@" in value or re.fullmatch(r"\+?\d[\d\s-]{9,}", value):
        return "sha256:" + hashlib.sha256(value.encode()).hexdigest()[:16]
    return value


def parent_of(path: str) -> str | None:
    """Guess which of our parent categories a category path belongs to (None = unknown)."""
    top = path.split(PATH_SEP)[0] if path else ""
    for parent, pattern in PARENT_KEYWORDS:
        if re.search(pattern, top, re.IGNORECASE):
            return parent
    for parent, pattern in PARENT_KEYWORDS:   # second chance: anywhere in the path
        if re.search(pattern, path or "", re.IGNORECASE):
            return parent
    return None


class IdMapper:
    """External id -> our id (U00001, P00001, ...), remembered for the id_map table."""

    def __init__(self, source: str):
        self.source = source
        self.maps: dict[str, dict[str, str]] = {e: {} for e in ENTITY_PREFIX}

    def add(self, entity: str, values: pd.Series) -> None:
        prefix, width = ENTITY_PREFIX[entity]
        known = self.maps[entity]
        for v in pd.unique(values[values != ""]):
            if v not in known:
                known[v] = f"{prefix}{len(known) + 1:0{width}d}"

    def apply(self, entity: str, values: pd.Series) -> pd.Series:
        known = self.maps[entity]
        return values.map(lambda v: known.get(v, "") if v != "" else "")

    def table(self) -> pd.DataFrame:
        rows = [(e, ours, self.source, _safe_external_id(str(ext)))
                for e, m in self.maps.items() for ext, ours in m.items()]
        return pd.DataFrame(rows, columns=["entity", "internal_id", "source", "external_id"])


# ============================================================================ building tables
def build_categories(products: pd.DataFrame, tracker: Tracker) -> tuple[pd.DataFrame, pd.Series]:
    """Category paths ('Clothing > Men > T-shirts') -> a category tree + each product's deepest id."""
    path = _text(_col(products, "category_path"))
    if "category_id" in products.columns:   # a plain category name/id column = a one-level path
        path = path.where(path != "", _text(products["category_id"]))
    rows, ids = [], {}
    for p in sorted(set(path) - {""}):
        parts = [x.strip() for x in p.split(PATH_SEP) if x.strip()]
        for depth in range(1, len(parts) + 1):
            key = PATH_SEP.join(parts[:depth])
            if key not in ids:
                ids[key] = f"C{len(ids) + 1:03d}"
                parent = ids[PATH_SEP.join(parts[: depth - 1])] if depth > 1 else ""
                rows.append({"category_id": ids[key], "name": parts[depth - 1], "parent_id": parent, "_path": key})
    deepest = path.map(lambda p: ids.get(PATH_SEP.join(x.strip() for x in p.split(PATH_SEP) if x.strip()), ""))
    missing = deepest == ""
    if missing.any():
        tracker.estimated["products.category_id = UNKNOWN"] += int(missing.sum())
        rows.append({"category_id": "UNKNOWN", "name": "Unknown", "parent_id": "", "_path": ""})
        deepest = deepest.where(~missing, "UNKNOWN")
    if rows:
        tracker.built.append(f"categories: {len(rows)} built from category paths")
    return pd.DataFrame(rows, columns=["category_id", "name", "parent_id", "_path"]), deepest


def clean_events(ev: pd.DataFrame, tracker: Tracker) -> pd.DataFrame:
    n = len(ev)
    ev = ev.copy()
    for c in ("user_id", "product_id", "session_id", "search_query", "source"):
        ev[c] = _text(_col(ev, c))
    ev["search_query"] = ev["search_query"].map(strip_html)
    if "event_type" not in ev.columns:
        # No event type in the data: ratings mean "bought it", anything else is a view.
        ev["event_type"] = np.where(_text(_col(ev, "rating")) != "", "purchase", "view")
        tracker.estimated["events.event_type (derived)"] += n
    ev["event_type"] = _text(ev["event_type"])
    ev["timestamp"] = _text(_col(ev, "timestamp"))

    bad_type = ~ev["event_type"].isin([e.value for e in EventType])
    tracker.drop("events", "unknown event type", bad_type.sum())
    no_user = ~bad_type & (ev["user_id"] == "")
    tracker.drop("events", "missing user_id", no_user.sum())
    no_time = ~bad_type & ~no_user & (ev["timestamp"] == "")
    tracker.drop("events", "missing or unreadable timestamp", no_time.sum())
    no_product = ~bad_type & ~no_user & ~no_time & (ev["product_id"] == "") & (ev["event_type"] != "search")
    tracker.drop("events", "missing product_id", no_product.sum())
    ev = ev[~(bad_type | no_user | no_time | no_product)]

    bad_source = ~ev["source"].isin([s.value for s in EventSource])
    tracker.estimated["events.source = home"] += int(bad_source.sum())
    ev.loc[bad_source, "source"] = "home"
    dup = ev.duplicated(["user_id", "product_id", "event_type", "timestamp"])
    tracker.drop("events", "duplicate (same user, product, type, time)", dup.sum())
    return ev[~dup].sort_values(["timestamp", "user_id"], kind="stable").reset_index(drop=True)


def clean_reviews(rv: pd.DataFrame, tracker: Tracker) -> pd.DataFrame:
    rv = rv.copy()
    for c in ("user_id", "product_id", "review_date"):
        rv[c] = _text(_col(rv, c))
    rv["rating"] = pd.to_numeric(_col(rv, "rating", None), errors="coerce").round().clip(1, 5)
    rv["review_text"] = _col(rv, "review_text").map(strip_html)
    bad = (rv["user_id"] == "") | (rv["product_id"] == "") | rv["rating"].isna()
    tracker.drop("reviews", "missing user, product or rating", bad.sum())
    rv = rv[~bad]
    dup = rv.duplicated(["user_id", "product_id", "review_date"])
    tracker.drop("reviews", "duplicate review", dup.sum())
    rv = rv[~dup].sort_values(["review_date", "user_id"], kind="stable").reset_index(drop=True)
    rv["rating"] = rv["rating"].astype(int)
    return rv


def build_sessions(ev: pd.DataFrame, gap_minutes: int, tracker: Tracker) -> pd.DataFrame:
    """Same user, less than 30 minutes between events = same session."""
    if ev.empty:
        return ev.assign(session_id=pd.Series(dtype=object))
    if (ev["session_id"] != "").all():
        return ev
    ts = pd.to_datetime(ev["timestamp"])
    order = np.lexsort((ts.to_numpy(), ev["user_id"].to_numpy()))
    e = ev.iloc[order]
    t = ts.iloc[order]
    new = (e["user_id"] != e["user_id"].shift()) | (t.diff() > pd.Timedelta(minutes=gap_minutes))
    key = new.cumsum().astype(str)
    ev = ev.copy()
    ev.loc[e.index, "session_id"] = "_s" + key.to_numpy()   # temporary ids; renumbered by start time later
    tracker.built.append(f"sessions: built from events (same user, gap < {gap_minutes} min)")
    return ev


def finish_sessions(ev: pd.DataFrame, users: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Sessions table from events; session ids renumbered S0000001... by start time."""
    if ev.empty:
        return ev, pd.DataFrame(columns=columns("sessions"))
    first = ev.groupby("session_id", sort=False).agg(user_id=("user_id", "first"),
                                                     start_time=("timestamp", "min")).reset_index()
    first = first.sort_values(["start_time", "session_id"], kind="stable").reset_index(drop=True)
    new_ids = {old: f"S{i + 1:07d}" for i, old in enumerate(first["session_id"])}
    device = dict(zip(users["user_id"], users["device"])) if "device" in users else {}
    sessions = pd.DataFrame({"session_id": first["session_id"].map(new_ids), "user_id": first["user_id"],
                             "start_time": first["start_time"],
                             "device": first["user_id"].map(lambda u: device.get(u, "unknown")), "is_gift": 0})
    ev = ev.assign(session_id=ev["session_id"].map(new_ids))
    return ev, sessions


def build_users(raw_users: pd.DataFrame | None, ev: pd.DataFrame, rv: pd.DataFrame, orders: pd.DataFrame,
                tracker: Tracker) -> pd.DataFrame:
    seen = pd.concat([ev[["user_id", "timestamp"]],
                      rv[["user_id", "review_date"]].rename(columns={"review_date": "timestamp"}),
                      orders[["user_id", "order_date"]].rename(columns={"order_date": "timestamp"})])
    seen = seen[seen["user_id"] != ""]
    first_seen = seen.groupby("user_id")["timestamp"].min()
    if raw_users is not None and len(raw_users):
        users = raw_users.copy()
        for c in columns("users"):
            users[c] = _text(_col(users, c))
        users = users[users["user_id"] != ""].drop_duplicates("user_id")
        extra = sorted(set(first_seen.index) - set(users["user_id"]))
        users = pd.concat([users, pd.DataFrame({"user_id": extra})], ignore_index=True).fillna("")
    else:
        users = pd.DataFrame({"user_id": first_seen.index})
        if len(users):
            tracker.built.append(f"users: {len(users)} created from distinct user_ids")
    for c in columns("users"):
        users[c] = _text(_col(users, c))
    no_signup = users["signup_date"] == ""
    users.loc[no_signup, "signup_date"] = (users.loc[no_signup, "user_id"].map(first_seen).fillna("")
                                           .astype(str).str[:10])
    tracker.estimated["users.signup_date = first event date"] += int(no_signup.sum())
    for c in ("age_group", "gender", "city", "device"):
        empty = users[c] == ""
        users.loc[empty, c] = "unknown"
        tracker.estimated[f"users.{c} = unknown"] += int(empty.sum())
    # city_tier is a number column: unknown stays empty (NULL) instead of the word 'unknown'.
    users["is_minor"] = np.where(users["is_minor"].isin(["1", "true", "True"]) | (users["age_group"] == "13-17"),
                                 1, 0)
    return users[columns("users")]


def build_products(raw: pd.DataFrame, used_ids: set[str], ev: pd.DataFrame, rv: pd.DataFrame,
                   tracker: Tracker) -> pd.DataFrame:
    p = raw.copy()
    p["product_id"] = _text(_col(p, "product_id"))
    bad = p["product_id"] == ""
    tracker.drop("products", "missing product_id", bad.sum())
    p = p[~bad]
    dup = p.duplicated("product_id")
    tracker.drop("products", "duplicate product_id", dup.sum())
    p = p[~dup]
    if used_ids:   # products only seen in events still need a catalog row
        missing = sorted(used_ids - set(p["product_id"]))
        if missing:
            tracker.estimated["products added for ids only seen in events"] += len(missing)
            p = pd.concat([p, pd.DataFrame({"product_id": missing})], ignore_index=True)
    p = p.reset_index(drop=True)

    title = _col(p, "title").map(strip_html)
    tracker.estimated["products.title = 'Product <id>'"] += int((title == "").sum())
    p["title"] = title.where(title != "", "Product " + p["product_id"])
    for c in ("description", "tags"):
        p[c] = _col(p, c).map(strip_html) if c == "description" else _text(_col(p, c))
    brand = _text(_col(p, "brand"))
    tracker.estimated["products.brand = Unknown"] += int((brand == "").sum())
    p["brand"] = brand.where(brand != "", "Unknown")

    # Ratings: from reviews when the catalog has none.
    stats = rv.groupby("product_id")["rating"].agg(["mean", "count"]) if len(rv) else pd.DataFrame()
    avg = pd.to_numeric(_col(p, "avg_rating", None), errors="coerce")
    cnt = pd.to_numeric(_col(p, "rating_count", None), errors="coerce")
    if len(stats):
        from_reviews = avg.isna() & p["product_id"].isin(stats.index)
        avg = avg.where(~from_reviews, p["product_id"].map(stats["mean"]).round(1))
        cnt = cnt.where(cnt.notna() | ~p["product_id"].isin(stats.index), p["product_id"].map(stats["count"]))
        tracker.estimated["products.avg_rating from reviews"] += int(from_reviews.sum())
    p["avg_rating"], p["rating_count"] = avg.clip(1, 5), cnt.astype("Int64")

    stock = pd.to_numeric(_col(p, "stock_qty", None), errors="coerce")
    tracker.estimated["products.stock_qty = 100"] += int(stock.isna().sum())
    p["stock_qty"] = stock.fillna(100).astype(int)
    first_seen = pd.concat([ev[["product_id", "timestamp"]],
                            rv[["product_id", "review_date"]].rename(columns={"review_date": "timestamp"})])
    first_seen = first_seen[first_seen["timestamp"] != ""].groupby("product_id")["timestamp"].min().str[:10]
    launch = _text(_col(p, "launch_date")).str[:10]
    no_launch = launch == ""
    p["launch_date"] = launch.where(~no_launch, p["product_id"].map(first_seen).astype(object).fillna(""))
    tracker.estimated["products.launch_date = first seen"] += int(no_launch.sum())
    p["age_restricted"] = _text(_col(p, "age_restricted")).isin(["1", "true", "True"]).astype(int)
    p["price"] = pd.to_numeric(_col(p, "price", None), errors="coerce")
    p["mrp"] = pd.to_numeric(_col(p, "mrp", None), errors="coerce")
    for c in ("margin_pct", "is_consumable", "repurchase_days"):
        p[c] = _col(p, c, None)
    return p


def fill_product_defaults(p: pd.DataFrame, categories: pd.DataFrame, tracker: Tracker) -> pd.DataFrame:
    """Price, mrp, margin, consumable: category-based defaults from DATA_STANDARD 'If missing'."""
    paths = dict(zip(categories["category_id"], categories["_path"])) if len(categories) else {}
    parent = p["category_id"].map(lambda c: parent_of(paths.get(c, "")))
    price = p["price"].where(p["price"] > 0)
    no_price = price.isna()
    if no_price.any() and price.notna().any():
        # Median of the product's own category; if that has no prices, of the parent, grand-parent, ...
        path = p["category_id"].map(lambda c: paths.get(c, ""))
        for depth in range(max([x.count(PATH_SEP) + 1 for x in path] + [1]), 0, -1):
            level = path.map(lambda x: PATH_SEP.join(x.split(PATH_SEP)[:depth]) if x else "")
            price = price.fillna(price.groupby(level).transform("median").where(level != ""))
        price = price.fillna(price.median())
        tracker.estimated["products.price = category median"] += int(no_price.sum())
    elif no_price.any():
        tracker.notes.append("No prices at all in this dataset: price-based features (budget, offers) are weak.")
    p["price"] = price.round(2)
    mrp = p["mrp"].where(p["mrp"] >= p["price"])
    tracker.estimated["products.mrp = price"] += int((mrp.isna() & p["price"].notna()).sum())
    p["mrp"] = mrp.fillna(p["price"])

    def default_margin(par: str | None) -> float:
        lo, hi = C.PARENT_MARGINS.get(par or "", (15.0, 25.0))
        return round((lo + hi) / 2, 1)
    margin = pd.to_numeric(p["margin_pct"], errors="coerce")
    tracker.estimated["products.margin_pct = category default"] += int(margin.isna().sum())
    p["margin_pct"] = margin.fillna(parent.map(default_margin))

    cons = pd.to_numeric(p["is_consumable"], errors="coerce")
    guess = parent.isin(list(CONSUMABLE_DAYS)).astype(int)
    tracker.estimated["products.is_consumable from category"] += int(cons.isna().sum())
    p["is_consumable"] = cons.fillna(guess).astype(int)
    days = pd.to_numeric(p["repurchase_days"], errors="coerce")
    days = days.fillna(parent.map(lambda x: CONSUMABLE_DAYS.get(x or "", 30)))
    p["repurchase_days"] = days.where(p["is_consumable"] == 1).astype("Int64")
    return p


def orders_from_purchases(ev: pd.DataFrame, products: pd.DataFrame, tracker: Tracker
                          ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """No order data: 1 order per user per day from purchase events."""
    buys = ev[ev["event_type"] == "purchase"]
    if buys.empty:
        return pd.DataFrame(columns=columns("orders")), pd.DataFrame(columns=columns("order_items"))
    price = dict(zip(products["product_id"], products["price"])) if len(products) else {}
    b = buys.assign(day=buys["timestamp"].str[:10])
    items = (b.groupby(["user_id", "day", "product_id"], sort=False)
             .agg(quantity=("event_id", "size"), first=("timestamp", "min")).reset_index())
    items["price_paid"] = items["product_id"].map(price)
    orders = (items.assign(line=items["quantity"] * items["price_paid"])
              .groupby(["user_id", "day"], sort=False)
              .agg(order_date=("first", "min"), total_amount=("line", lambda s: s.sum(min_count=1)))
              .reset_index().sort_values(["order_date", "user_id"], kind="stable").reset_index(drop=True))
    orders["order_id"] = [f"O{i + 1:07d}" for i in range(len(orders))]
    items = items.merge(orders[["user_id", "day", "order_id"]], on=["user_id", "day"])
    tracker.built.append(f"orders: {len(orders)} built from purchase events (1 per user per day)")
    return (orders[columns("orders")],
            items.sort_values(["order_id", "product_id"], kind="stable")[columns("order_items")])


# ============================================================================ --fill-missing
def fill_user_fields(users: pd.DataFrame, seed: int, tracker: Tracker) -> pd.DataFrame:
    """Unknown demographics -> random values from our generator's distributions (marked estimated)."""
    rng = np.random.default_rng(seed)
    from data_gen.users import TIER1, TIER2, tier3_cities

    def pick(options: dict, n: int) -> list:
        keys = list(options)
        probs = np.array(list(options.values()), float)
        return [keys[i] for i in rng.choice(len(keys), size=n, p=probs / probs.sum())]

    users = users.copy()
    for col, options in (("age_group", C.AGE_GROUPS), ("gender", C.GENDERS), ("device", C.DEVICES)):
        unknown = users[col] == "unknown"
        users.loc[unknown, col] = pick(options, int(unknown.sum()))
        tracker.estimated[f"users.{col} (--fill-missing)"] += int(unknown.sum())
    unknown = users["city"] == "unknown"
    tiers = pick(C.CITY_TIERS, int(unknown.sum()))
    cities = {1: TIER1, 2: TIER2, 3: tier3_cities(seed)}
    users.loc[unknown, "city_tier"] = [str(t) for t in tiers]
    users.loc[unknown, "city"] = [cities[t][rng.integers(len(cities[t]))] for t in tiers]
    tracker.estimated["users.city + city_tier (--fill-missing)"] += int(unknown.sum())
    users["is_minor"] = (users["age_group"] == "13-17").astype(int)
    return users


def simulate_shoppers(categories: pd.DataFrame, products: pd.DataFrame, n_users: int, seed: int,
                      tracker: Tracker, out_dir: Path) -> dict[str, pd.DataFrame]:
    """Catalog-only data: simulate shoppers ON the real catalog with our generator."""
    from data_gen.offers import build_consent, build_offers
    from data_gen.orders import build_orders, build_reviews
    from data_gen.relations import build_relations
    from data_gen.simulate import Simulator
    from data_gen.users import build_users as gen_users
    from data_gen.utils import make_rngs, write_ground_truth

    rngs = make_rngs(seed)
    p = products.copy()
    p["price"] = p["price"].fillna(499.0)
    p["mrp"] = p["mrp"].fillna(p["price"])
    # Popularity from rating counts when the catalog has them, else a random long tail.
    counts = pd.to_numeric(p["rating_count"], errors="coerce").fillna(0).to_numpy()
    rank = (np.argsort(np.argsort(-counts, kind="stable"), kind="stable") if counts.any()
            else rngs["catalog"].permutation(len(p)))
    p["_pop"] = 1.0 / (rank + 1) ** C.ZIPF_EXPONENT
    p["_quality"] = pd.to_numeric(p["avg_rating"], errors="coerce").fillna(C.RATING_MEAN)
    p["_query"] = p["category_id"].map(dict(zip(categories["category_id"], categories["name"]))).fillna("").str.lower()
    cats = categories.drop(columns=["_path"], errors="ignore")
    relations = build_relations(p, rngs["relations"])
    users, hidden = gen_users(n_users, cats, p, rngs["users"], seed)
    offers = build_offers(cats, p, relations, rngs["offers"])
    consent = build_consent(users, hidden, rngs["consent"])
    sim = Simulator(p, cats, users, hidden, relations, offers, consent, C.SIZES["small"]["activity_scale"],
                    rngs["simulate"]).run()
    orders, items = build_orders(sim.purchases)
    reviews = build_reviews(items, p, rngs["reviews"])
    write_ground_truth(hidden, relations, out_dir / "ground_truth")
    tracker.built.append(f"--fill-missing: simulated {n_users} shoppers on the real catalog "
                         f"({len(sim.events):,} events); ground truth in {out_dir / 'ground_truth'}")
    return {"users": users, "events": sim.events, "sessions": sim.sessions, "orders": orders,
            "order_items": items, "reviews": reviews, "offers": offers, "user_consent": consent,
            "offer_sends": sim.offer_sends}


# ============================================================================ main flow
def run(mapping: str | Path, input_dir: str | Path | None = None, out_dir: str | Path | None = None,
        sample: int | None = None, fill_missing: bool = False, db_path: str | Path | None = None,
        load_db: bool = True, seed: int = C.SEED, chunksize: int = 200_000) -> RunResult:
    cfg = load_mapping(mapping)
    source = str(cfg["source"])
    tracker = Tracker()
    input_dir = Path(input_dir) if input_dir else None
    out = Path(out_dir) if out_dir else PROCESSED / source
    if not out.is_absolute():
        out = PROJECT_ROOT / out

    # 1-2. Read + map + transform every raw file.
    raw = {t: read_table(t, spec, cfg, input_dir, chunksize, tracker) for t, spec in cfg["files"].items()}
    keep_ids = bool(cfg.get("keep_ids", False))

    if keep_ids:
        tables = {t: df.astype(object).where(df.notna(), "") for t, df in raw.items()}
        tracker.built.append("keep_ids: ids are already in our format, copied as they are")
        return _finish(cfg, source, out, tables, tracker, fill_missing, db_path, load_db, seed, mapping)

    ev = raw.get("events", pd.DataFrame(columns=["user_id", "product_id", "timestamp", "event_type"]))
    rv = raw.get("reviews", pd.DataFrame(columns=["user_id", "product_id", "rating", "review_date"]))
    ev = clean_events(ev, tracker) if len(ev) or "events" in raw else ev
    rv = clean_reviews(rv, tracker) if len(rv) else rv.assign(review_text="")

    # Reviews with a rating also mean "this user bought it" -> purchase events.
    if len(rv):
        dated = rv[rv["review_date"] != ""]
        derived = pd.DataFrame({"user_id": dated["user_id"], "product_id": dated["product_id"],
                                "event_type": "purchase", "timestamp": dated["review_date"], "source": "home",
                                "search_query": "", "session_id": ""})
        before = len(ev)
        ev = clean_events(pd.concat([ev, derived], ignore_index=True), Tracker())
        if len(ev) > before:
            tracker.built.append(f"events: {len(ev) - before} purchase events derived from reviews")
    for c in ("session_id", "source", "search_query"):
        ev[c] = _text(_col(ev, c))

    # Raw orders (if any) become purchase events too.
    raw_orders = raw.get("orders")
    raw_items = raw.get("order_items")

    # 3b. --sample N: keep N random users (same seed -> same users).
    if sample:
        pool = pd.unique(pd.concat([ev["user_id"], rv["user_id"]]))
        pool = np.array(sorted(pool[pool != ""]))
        keep = set(np.random.default_rng(seed).choice(pool, size=min(sample, len(pool)), replace=False))
        tracker.drop("events", f"--sample {sample} (other users)", int((~ev["user_id"].isin(keep)).sum()))
        ev, rv = ev[ev["user_id"].isin(keep)], rv[rv["user_id"].isin(keep)]
        for name in ("users", "orders"):
            if name in raw and "user_id" in raw[name]:
                raw[name] = raw[name][_text(raw[name]["user_id"]).isin(keep)]

    # 4. Map ids to ours (U..., P..., S..., O...).
    ids = IdMapper(source)
    ids.add("user", ev["user_id"])
    ids.add("user", rv["user_id"])
    if "users" in raw:
        ids.add("user", _text(_col(raw["users"], "user_id")))
    ext_products = raw.get("products")
    if ext_products is not None:
        catalog_ids = _text(_col(ext_products, "product_id"))
        used_ext = (set(ev["product_id"]) | set(rv["product_id"])) - {""}
        if used_ext:   # interaction data exists: keep only products somebody interacted with
            trimmed = ~catalog_ids.isin(used_ext) & (catalog_ids != "")
            tracker.drop("products", "never seen in events/reviews (catalog trimmed)", trimmed.sum())
            ext_products, catalog_ids = ext_products[~trimmed], catalog_ids[~trimmed]
        ids.add("product", catalog_ids)
    ids.add("product", ev["product_id"])
    ids.add("product", rv["product_id"])
    if (ev["session_id"] != "").any():
        ids.add("session", ev["session_id"])
    ev = ev.assign(user_id=ids.apply("user", ev["user_id"]), product_id=ids.apply("product", ev["product_id"]))
    if (ev["session_id"] != "").any():
        ev["session_id"] = ids.apply("session", ev["session_id"])
    rv = rv.assign(user_id=ids.apply("user", rv["user_id"]), product_id=ids.apply("product", rv["product_id"]))
    if raw_orders is not None and raw_items is not None:
        ids.add("order", _text(_col(raw_orders, "order_id")))
        raw_orders = raw_orders.assign(order_id=ids.apply("order", _text(raw_orders["order_id"])),
                                       user_id=ids.apply("user", _text(_col(raw_orders, "user_id"))))
        raw_items = raw_items.assign(order_id=ids.apply("order", _text(raw_items["order_id"])),
                                     product_id=ids.apply("product", _text(_col(raw_items, "product_id"))))

    # 5. Products + categories (only when the source has a catalog).
    products = categories = None
    if ext_products is not None:
        ext_products = ext_products.assign(product_id=ids.apply("product", _text(_col(ext_products, "product_id"))))
        used = set(ev["product_id"]) | set(rv["product_id"])
        products = build_products(ext_products, used - {""}, ev, rv, tracker)
        categories, products["category_id"] = build_categories(products, tracker)
        products = fill_product_defaults(products, categories, tracker)
        cat_rows = categories.loc[categories["_path"] != "", ["category_id", "_path"]]
        id_map_extra = pd.DataFrame({"entity": "category", "internal_id": cat_rows["category_id"],
                                     "source": source, "external_id": cat_rows["_path"]})
    else:
        id_map_extra = pd.DataFrame(columns=["entity", "internal_id", "source", "external_id"])

    # Orders: given ones, or built from purchase events.
    if raw_orders is not None and raw_items is not None:
        orders = raw_orders.assign(order_date=_text(_col(raw_orders, "order_date")))
        items = raw_items
        tracker.notes.append("orders/order_items taken from the source")
    else:
        ev = ev.assign(event_id=[f"E{i + 1:08d}" for i in range(len(ev))])
        orders, items = orders_from_purchases(ev, products if products is not None else pd.DataFrame(), tracker)

    # Sessions (built when the source has none), then final event ids.
    ev = build_sessions(ev, int(cfg.get("session_gap_minutes", 30)), tracker)
    users = build_users(raw.get("users").assign(user_id=ids.apply("user", _text(raw["users"]["user_id"])))
                        if "users" in raw else None, ev, rv, orders, tracker)
    ev, sessions = finish_sessions(ev, users)
    ev = ev.sort_values(["timestamp", "session_id"], kind="stable").reset_index(drop=True)
    ev["event_id"] = [f"E{i + 1:08d}" for i in range(len(ev))]
    if len(rv):
        rv = rv.assign(review_id=[f"R{i + 1:06d}" for i in range(len(rv))])

    tables: dict[str, pd.DataFrame] = {"events": ev, "users": users, "sessions": sessions,
                                       "orders": orders, "order_items": items,
                                       "id_map": pd.concat([ids.table(), id_map_extra], ignore_index=True)}
    if len(rv):
        tables["reviews"] = rv
    if products is not None:
        tables["products"] = products
        tables["categories"] = categories
    return _finish(cfg, source, out, tables, tracker, fill_missing, db_path, load_db, seed, mapping)


def _finish(cfg: dict, source: str, out: Path, tables: dict[str, pd.DataFrame], tracker: Tracker,
            fill_missing: bool, db_path: str | Path | None, load_db: bool, seed: int, mapping) -> RunResult:
    if fill_missing:
        _fill_missing(cfg, tables, tracker, seed, out)

    for name in TABLES:   # every contract table is written (empty ones too) so loading replaces old data
        if name in tables:
            df = tables[name]
            for c in columns(name):
                if c not in df.columns:
                    df[c] = ""
            tables[name] = df
    write_tables(tables, out)
    report = validate_folder(out, strict=False)
    write_report(out, source, cfg, mapping, tracker, report)
    if load_db and report.ok:
        load_csvs_to_db(out, db_path=db_path)
    return RunResult(out, tables, tracker, report)


def _fill_missing(cfg: dict, tables: dict[str, pd.DataFrame], tracker: Tracker, seed: int, out: Path) -> None:
    from data_gen.offers import build_consent, build_offers

    products, categories = tables.get("products"), tables.get("categories")
    if (tables.get("events") is None or tables["events"].empty) and products is not None and len(products):
        sim = simulate_shoppers(categories, products, int(cfg.get("simulate_users", 1000)), seed, tracker, out)
        tables.update(sim)
        tables["categories"] = categories.drop(columns=["_path"], errors="ignore")
        return
    users = tables.get("users")
    if users is not None and len(users):
        tables["users"] = users = fill_user_fields(users, seed, tracker)
    if users is None or users.empty:
        return
    rng = np.random.default_rng(seed)
    cats = categories if categories is not None else pd.DataFrame(columns=["category_id", "name", "parent_id"])
    prods = products if products is not None else pd.DataFrame(
        columns=["product_id", "category_id", "is_consumable", "price"])
    empty_rel = pd.DataFrame(columns=["product_id", "related_product_id", "relation"])
    if "offers" not in tables or tables["offers"].empty:
        tables["offers"] = build_offers(cats, prods, empty_rel, rng)
        tracker.built.append(f"--fill-missing: {len(tables['offers'])} offers from our offer templates")
    if "user_consent" not in tables or tables["user_consent"].empty:
        hidden = pd.DataFrame({"user_id": users["user_id"], "preferred_categories": ""})
        tables["user_consent"] = build_consent(users, hidden, rng)
        tracker.built.append("--fill-missing: user_consent (~60% opted in, minors never)")


def write_report(out: Path, source: str, cfg: dict, mapping, tracker: Tracker, report) -> None:
    lines = [f"# Ingest report: {source}", "", f"- Mapping: `{mapping}`"]
    if cfg.get("licence"):
        lines.append(f"- Licence / citation: {cfg['licence']}")
    lines += ["", "## Rows in / out", "", "| Table | Raw rows in | Standard rows out |", "|---|---:|---:|"]
    for t in TABLES:
        n_in, n_out = tracker.rows_in.get(t, 0), report.counts.get(t, 0)
        if n_in or n_out:
            lines.append(f"| {t} | {n_in:,} | {n_out:,} |")
    lines += ["", "## Dropped rows", ""]
    lines += [f"- {t}: {reason} — {n:,}" for t, c in tracker.drops.items() for reason, n in c.items()] or ["- none"]
    lines += ["", "## Estimated / default values", ""]
    lines += [f"- {k}: {v:,}" for k, v in tracker.estimated.items() if v] or ["- none"]
    lines += ["", "## Built automatically", ""] + ([f"- {b}" for b in tracker.built] or ["- nothing"])
    if tracker.pii_columns:
        lines += ["", "## Personal data", "",
                  f"- Raw columns that look personal were NOT copied: {', '.join(sorted(tracker.pii_columns))}"]
    if tracker.notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in tracker.notes]
    lines += [""] + format_report(report)
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert a raw dataset to the Standard Format and load it.")
    parser.add_argument("--mapping", required=True, help="ingest/mappings/<source>.yaml")
    parser.add_argument("--input", type=Path, help="folder with the raw files (overrides the folders in the mapping)")
    parser.add_argument("--out", type=Path, help="output folder (default data/processed/<source>/)")
    parser.add_argument("--sample", type=int, help="keep N random users (fast testing on huge files)")
    parser.add_argument("--fill-missing", action="store_true",
                        help="add offers/consent/demographics (or simulated shoppers) from our generator")
    parser.add_argument("--db", type=Path, help="DB file to load into (default: DB_PATH in .env)")
    parser.add_argument("--no-db", action="store_true", help="only write CSVs + report")
    parser.add_argument("--seed", type=int, default=C.SEED)
    args = parser.parse_args(argv)

    result = run(args.mapping, args.input, args.out, args.sample, args.fill_missing, args.db, not args.no_db,
                 args.seed)
    rep = result.report
    print(f"Wrote Standard Format CSVs + report to {result.out_dir}")
    print(f"Data level: {rep.level} | validation {'PASSED' if rep.ok else 'FAILED'}")
    for e in rep.errors:
        print(f"  ERROR: {e}")
    if rep.ok and not args.no_db:
        print(f"Loaded into {args.db or settings.DB_PATH}. To use it: DATA_SOURCE=external, "
              f"EXTERNAL_SOURCE={result.out_dir.name} in .env")
    print(f"Full report: {result.out_dir / 'report.md'}")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
