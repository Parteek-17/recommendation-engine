"""Validation report for any folder of Standard Format CSVs (ours or converted real data).

    python -m data_gen.validate                         # checks data/raw/ (+ data/ground_truth/)
    python -m data_gen.validate --folder data/processed/retailrocket --lenient

Checks: every column matches the Data Contract, ids/enums/booleans/timestamps are valid,
no broken foreign keys, prices/ratings make sense. Prints row counts, the event funnel,
top categories and long-tail stats, and detects the data level (L1-L4) + features ON/OFF.

strict=True (our synthetic data) also requires sensible funnel rates, a long tail, events
inside the data window and ground truth kept OUT of the raw folder. External data runs
with strict=False: those become warnings, because real data looks however it looks.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from common.config import PROJECT_ROOT
from common.db import FEATURE_LEVELS, LEVEL_REQUIREMENTS, TABLES, columns
from common.schemas import DATA_END, DATA_START, EventSource, EventType, OfferType, SendStatus, TriggerType

ID_PATTERNS = {   # table -> (id column, regex). \d{n,} also allows bigger converted datasets.
    "users": ("user_id", r"U\d{5,}"),
    "products": ("product_id", r"P\d{5,}"),
    "categories": ("category_id", r"C\d{3,}|UNKNOWN"),
    "sessions": ("session_id", r"S\d{7,}"),
    "events": ("event_id", r"E\d{8,}"),
    "orders": ("order_id", r"O\d{7,}"),
    "offers": ("offer_id", r"OF\d{4,}"),
    "reviews": ("review_id", r"R\d{6,}"),
    "offer_sends": ("send_id", r"SN\d{6,}"),
}
UNIQUE_KEYS = {"users": ["user_id"], "categories": ["category_id"], "products": ["product_id"],
               "sessions": ["session_id"], "events": ["event_id"], "orders": ["order_id"],
               "reviews": ["review_id"], "offers": ["offer_id"], "offer_sends": ["send_id"],
               "user_consent": ["user_id"], "telegram_links": ["user_id"]}
ENUMS = {
    ("events", "event_type"): {e.value for e in EventType},
    ("events", "source"): {e.value for e in EventSource},
    ("offers", "offer_type"): {e.value for e in OfferType},
    ("offers", "trigger_type"): {e.value for e in TriggerType},
    ("offer_sends", "trigger_type"): {e.value for e in TriggerType},
    ("offer_sends", "status"): {e.value for e in SendStatus},
}
BOOLEANS = [("users", "is_minor"), ("products", "is_consumable"), ("products", "age_restricted"),
            ("sessions", "is_gift"), ("offers", "is_active"), ("offers", "requires_approval"),
            ("user_consent", "opted_in"), ("telegram_links", "active")]
TIMESTAMPS = [("users", "signup_date"), ("products", "launch_date"), ("sessions", "start_time"),
              ("events", "timestamp"), ("orders", "order_date"), ("reviews", "review_date"),
              ("offers", "start_at"), ("offers", "expires_at"), ("offer_sends", "sent_at"),
              ("offer_sends", "updated_at"), ("offer_rankings", "created_at"), ("telegram_links", "linked_at")]
# (child table, column, parent table, parent column)
FOREIGN_KEYS = [
    ("events", "user_id", "users", "user_id"), ("events", "product_id", "products", "product_id"),
    ("events", "session_id", "sessions", "session_id"), ("sessions", "user_id", "users", "user_id"),
    ("orders", "user_id", "users", "user_id"), ("order_items", "order_id", "orders", "order_id"),
    ("order_items", "product_id", "products", "product_id"), ("reviews", "user_id", "users", "user_id"),
    ("reviews", "product_id", "products", "product_id"), ("products", "category_id", "categories", "category_id"),
    ("categories", "parent_id", "categories", "category_id"), ("offers", "category_id", "categories", "category_id"),
    ("offers", "product_id", "products", "product_id"), ("offers", "bundle_product_id", "products", "product_id"),
    ("offer_sends", "user_id", "users", "user_id"), ("offer_sends", "offer_id", "offers", "offer_id"),
    ("offer_rankings", "user_id", "users", "user_id"), ("offer_rankings", "offer_id", "offers", "offer_id"),
    ("user_consent", "user_id", "users", "user_id"), ("telegram_links", "user_id", "users", "user_id"),
]


@dataclass
class Report:
    folder: Path
    counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, object] = field(default_factory=dict)
    level: str = "L0"
    features: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def load_folder(folder: Path) -> dict[str, pd.DataFrame]:
    """Read every contract CSV that exists (as text, so ids stay exactly as written)."""
    tables = {}
    for table in TABLES:
        path = folder / f"{table}.csv"
        if path.exists():
            tables[table] = pd.read_csv(path, dtype=str, keep_default_na=False)
    return tables


def detect_level(counts: dict[str, int]) -> tuple[str, dict[str, bool]]:
    """Same rules as common.db.get_data_level, but on CSV row counts (before loading)."""
    level = "L0"
    for name, required in LEVEL_REQUIREMENTS.items():
        if all(counts.get(t, 0) > 0 for t in required):
            level = name
        else:
            break
    return level, {f: level >= need for f, need in FEATURE_LEVELS.items()}


def _sample(values: pd.Series, n: int = 3) -> str:
    return ", ".join(map(repr, values.head(n).tolist()))


def _check_schema(t: dict[str, pd.DataFrame], rep: Report) -> None:
    for table in TABLES:
        if table not in t:
            rep.warnings.append(f"{table}.csv missing (treated as empty)")
            continue
        got, want = list(t[table].columns), columns(table)
        if set(got) != set(want):
            missing, extra = sorted(set(want) - set(got)), sorted(set(got) - set(want))
            rep.errors.append(f"{table}: columns differ from the Data Contract (missing {missing}, extra {extra})")


def _check_values(t: dict[str, pd.DataFrame], rep: Report, strict: bool) -> None:
    for table, (col, pattern) in ID_PATTERNS.items():
        df = t.get(table)
        if df is None or df.empty or col not in df:
            continue
        bad = df.loc[~df[col].str.fullmatch(pattern), col]
        if len(bad):
            rep.errors.append(f"{table}.{col}: {len(bad)} ids don't look like {pattern} (e.g. {_sample(bad)})")
    for table, keys in UNIQUE_KEYS.items():
        df = t.get(table)
        if df is not None and not df.empty and set(keys) <= set(df.columns):
            dups = df.duplicated(keys).sum()
            if dups:
                rep.errors.append(f"{table}: {dups} duplicate {'+'.join(keys)}")
    for (table, col), allowed in ENUMS.items():
        df = t.get(table)
        if df is not None and not df.empty and col in df:
            bad = df.loc[~df[col].isin(allowed), col]
            if len(bad):
                rep.errors.append(f"{table}.{col}: {len(bad)} values outside the enum (e.g. {_sample(bad)})")
    for table, col in BOOLEANS:
        df = t.get(table)
        if df is not None and not df.empty and col in df:
            bad = df.loc[~df[col].isin(["0", "1"]), col]
            if len(bad):
                rep.errors.append(f"{table}.{col}: {len(bad)} values are not 0/1 (e.g. {_sample(bad)})")
    for table, col in TIMESTAMPS:
        df = t.get(table)
        if df is None or df.empty or col not in df:
            continue
        filled = df.loc[df[col] != "", col]
        parsed = pd.to_datetime(filled, format="ISO8601", errors="coerce")
        bad = filled[parsed.isna()]
        if len(bad):
            rep.errors.append(f"{table}.{col}: {len(bad)} values are not ISO-8601 (e.g. {_sample(bad)})")
        elif filled.str.contains(r"[+Z]|[+-]\d\d:\d\d$", regex=True).any():
            rep.errors.append(f"{table}.{col}: timestamps must be local time WITHOUT a timezone offset")

    p = t.get("products")
    if p is not None and not p.empty:
        price = pd.to_numeric(p["price"], errors="coerce")
        mrp = pd.to_numeric(p["mrp"], errors="coerce")
        if (price <= 0).any():
            rep.errors.append(f"products.price: {int((price <= 0).sum())} prices <= 0")
        if price.isna().any():   # real catalogs may have no prices at all: budget features get weaker
            (rep.errors if strict else rep.warnings).append(
                f"products.price: {int(price.isna().sum())} products without a price")
        if (mrp < price).any():
            rep.errors.append(f"products: {int((mrp < price).sum())} rows with mrp < price")
        rating = pd.to_numeric(p["avg_rating"], errors="coerce")
        if ((rating < 1) | (rating > 5)).any():
            rep.errors.append("products.avg_rating outside 1-5")
    r = t.get("reviews")
    if r is not None and not r.empty and not r["rating"].isin(["1", "2", "3", "4", "5"]).all():
        rep.errors.append("reviews.rating must be a whole number 1-5")
    e = t.get("events")
    if e is not None and not e.empty:
        no_product = (e["product_id"] == "") & (e["event_type"] != "search")
        if no_product.any():
            rep.errors.append(f"events: {int(no_product.sum())} non-search events without product_id")


def _check_foreign_keys(t: dict[str, pd.DataFrame], rep: Report) -> None:
    for child, col, parent, pcol in FOREIGN_KEYS:
        c, p = t.get(child), t.get(parent)
        if c is None or c.empty or p is None or p.empty or col not in c:
            continue   # a missing parent table is a lower data level, not an error
        values = c.loc[c[col] != "", col]
        broken = values[~values.isin(set(p[pcol]))]
        if len(broken):
            rep.errors.append(f"{child}.{col} -> {parent}.{pcol}: {len(broken)} broken links "
                              f"(e.g. {_sample(broken.drop_duplicates())})")


def _stats(t: dict[str, pd.DataFrame], rep: Report, strict: bool) -> None:
    e = t.get("events")
    if e is None or e.empty:
        return
    counts = e["event_type"].value_counts()
    n = {k: int(counts.get(k, 0)) for k in [x.value for x in EventType]}
    rep.stats["event_types"] = n

    def pct(a: int, b: int) -> float:
        return round(100 * a / b, 1) if b else 0.0

    funnel = {"view -> click %": pct(n["click"], n["view"]), "view -> add_to_cart %": pct(n["add_to_cart"], n["view"]),
              "add_to_cart -> purchase %": pct(n["purchase"], n["add_to_cart"]),
              "add_to_cart -> remove %": pct(n["remove_from_cart"], n["add_to_cart"]),
              "view -> wishlist %": pct(n["wishlist"], n["view"])}
    rep.stats["funnel"] = funnel
    if n["view"] and n["add_to_cart"]:
        problems = []
        if not 3 <= funnel["view -> add_to_cart %"] <= 30:
            problems.append(f"view->add_to_cart {funnel['view -> add_to_cart %']}% (expected 3-30%)")
        if not 15 <= funnel["add_to_cart -> purchase %"] <= 70:
            problems.append(f"add_to_cart->purchase {funnel['add_to_cart -> purchase %']}% (expected 15-70%)")
        for msg in problems:
            (rep.errors if strict else rep.warnings).append(f"funnel: {msg}")

    views = e.loc[e["event_type"] == "view", "product_id"].value_counts()
    p = t.get("products")
    n_products = len(p) if p is not None and not p.empty else e["product_id"].replace("", pd.NA).nunique()
    if len(views):
        top20 = views.iloc[: max(1, int(round(0.2 * n_products)))].sum() / views.sum()
        top1 = views.iloc[: max(1, int(round(0.01 * n_products)))].sum() / views.sum()
        rep.stats["long_tail"] = {
            "products with views": f"{len(views):,} of {n_products:,}",
            "share of views, top 1% products": f"{100 * top1:.1f}%",
            "share of views, top 20% products": f"{100 * top20:.1f}%",
            "median views per viewed product": int(views.median()),
            "max views (one product)": int(views.max()),
        }
        if strict and top20 < 0.5:
            rep.errors.append(f"long tail too flat: top 20% of products get only {100 * top20:.0f}% of views")

    if p is not None and not p.empty:
        cats = t.get("categories")
        names = dict(zip(cats["category_id"], cats["name"])) if cats is not None else {}
        per_cat = e.loc[e["product_id"] != ""].merge(p[["product_id", "category_id"]], on="product_id")
        top = per_cat["category_id"].value_counts().head(8)
        rep.stats["top_categories"] = {f"{c} {names.get(c, '')}".strip(): f"{100 * v / len(per_cat):.1f}%"
                                       for c, v in top.items()}

    if strict:
        ts = e["timestamp"]
        outside = ((ts < DATA_START) | (ts > DATA_END + "T23:59:59")).sum()
        if outside:
            rep.errors.append(f"events: {outside} outside the data window {DATA_START} .. {DATA_END}")
        u, p2 = t.get("users"), t.get("products")
        if u is not None and p2 is not None and not u.empty and not p2.empty:
            minors = set(u.loc[u["is_minor"] == "1", "user_id"])
            adult = set(p2.loc[p2["age_restricted"] == "1", "product_id"])
            leaked = e[e["user_id"].isin(minors) & e["product_id"].isin(adult)]
            if len(leaked):
                rep.errors.append(f"{len(leaked)} events of minors on age-restricted products")
            launch = e["product_id"].map(dict(zip(p2["product_id"], p2["launch_date"]))).fillna("")
            early = (launch != "") & (e["timestamp"].str[:10] < launch)
            if early.any():
                rep.errors.append(f"{int(early.sum())} events on products before their launch_date")


def _check_ground_truth(raw: Path, gt: Path | None, t: dict[str, pd.DataFrame], rep: Report) -> None:
    for name in ("user_hidden_profile", "product_relations"):
        if (raw / f"{name}.csv").exists():
            rep.errors.append(f"ground truth {name}.csv is inside {raw} — models could read it! Move it out.")
    if gt is None:
        return
    want = {"user_hidden_profile": ["user_id", "preferred_categories", "price_sensitivity", "activity_level",
                                    "is_gifter"],
            "product_relations": ["product_id", "related_product_id", "relation"]}
    for name, cols in want.items():
        path = gt / f"{name}.csv"
        if not path.exists():
            rep.errors.append(f"ground truth {path} missing")
            continue
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        if list(df.columns) != cols:
            rep.errors.append(f"ground truth {name}: columns {list(df.columns)} != {cols}")
            continue
        rep.counts[f"(ground truth) {name}"] = len(df)
        if name == "product_relations":
            if not df["relation"].isin(["complement", "substitute"]).all():
                rep.errors.append("product_relations.relation must be complement|substitute")
            if "products" in t:
                ids = set(t["products"]["product_id"])
                if not (df["product_id"].isin(ids) & df["related_product_id"].isin(ids)).all():
                    rep.errors.append("product_relations has product ids that are not in products.csv")
        elif "users" in t and not df["user_id"].isin(set(t["users"]["user_id"])).all():
            rep.errors.append("user_hidden_profile has user ids that are not in users.csv")


def validate_folder(folder: str | Path, ground_truth: str | Path | None = None, strict: bool = True) -> Report:
    folder = Path(folder)
    if not folder.is_absolute():
        folder = PROJECT_ROOT / folder
    rep = Report(folder=folder)
    if not folder.exists():
        rep.errors.append(f"folder not found: {folder}")
        return rep
    t = load_folder(folder)
    rep.counts = {name: len(df) for name, df in t.items()}
    _check_schema(t, rep)
    if rep.errors:   # wrong columns: the other checks would only add noise
        return rep
    _check_values(t, rep, strict)
    _check_foreign_keys(t, rep)
    _stats(t, rep, strict)
    gt = Path(ground_truth) if ground_truth else None
    if gt is not None and not gt.is_absolute():
        gt = PROJECT_ROOT / gt
    if strict or gt is not None:
        _check_ground_truth(folder, gt, t, rep)
    rep.level, rep.features = detect_level(rep.counts)
    return rep


def format_report(rep: Report) -> list[str]:
    """The report as markdown lines (printed by the CLI, saved as report.md by ingest)."""
    lines = [f"## Validation: {'PASSED' if rep.ok else 'FAILED'}", "", f"Folder: `{rep.folder}`", "",
             f"**Data level: {rep.level}**", "", "| Table | Rows |", "|---|---:|"]
    lines += [f"| {k} | {v:,} |" for k, v in rep.counts.items()]
    for title, key in (("Event types", "event_types"), ("Funnel", "funnel"), ("Long tail", "long_tail"),
                       ("Top categories (share of events)", "top_categories")):
        if key in rep.stats:
            lines += ["", f"### {title}", ""] + [f"- {k}: {v}" for k, v in rep.stats[key].items()]
    on = [f for f, v in rep.features.items() if v]
    off = [f for f, v in rep.features.items() if not v]
    lines += ["", "### Features", "", f"- ON: {', '.join(on) or '(none)'}", f"- OFF: {', '.join(off) or '(none)'}"]
    if rep.errors:
        lines += ["", "### Errors", ""] + [f"- {e}" for e in rep.errors]
    if rep.warnings:
        lines += ["", "### Warnings", ""] + [f"- {w}" for w in rep.warnings]
    return lines


def print_report(rep: Report) -> None:
    print("\n".join(format_report(rep)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a folder of Standard Format CSVs.")
    parser.add_argument("--folder", default="data/raw")
    parser.add_argument("--ground-truth", default=None,
                        help="ground-truth folder (default: data/ground_truth when checking data/raw)")
    parser.add_argument("--lenient", action="store_true", help="external data: funnel/long-tail only warn")
    args = parser.parse_args(argv)
    gt = args.ground_truth
    if gt is None and Path(args.folder).as_posix().rstrip("/") == "data/raw":
        gt = "data/ground_truth"
    rep = validate_folder(args.folder, ground_truth=gt, strict=not args.lenient)
    print_report(rep)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
