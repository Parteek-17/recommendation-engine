"""Uplift analysis: are we giving big discounts to people who would buy anyway?

    python -m offers.analysis            # writes offers/analysis.md

From `offer_sends` history we compute the redemption rate by
    discount size (small <10%, medium 10–20%, large >20%)  x  price sensitivity (low/medium/high)
and compare it with each group's ORGANIC purchase rate (chance of ordering in any 7-day
window with no offer at all). Rough uplift = redemption rate − organic rate.

If low-sensitivity users redeem at about their organic rate, or a bigger discount doesn't
raise their redemption, the discount is mostly a gift to someone who'd buy anyway —
that's why the generator gives them 0.7x the base discount (rules.SENSITIVITY_MULTIPLIER).

Price sensitivity here is BEHAVIOURAL (the rec engine's budget band), the same signal the
generator uses — not the hidden ground truth.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from common.config import PROJECT_ROOT
from common.db import read_table
from common.logging_utils import get_logger, now_iso
from offers import store
from offers.rules import discount_pct_equivalent
from offers.segments import budget_band

log = get_logger(__name__)

DEFAULT_OUTPUT = PROJECT_ROOT / "offers" / "analysis.md"
DELIVERED = ("sent", "opened", "clicked", "redeemed")  # reached the user (queued/blocked/failed didn't)
SENSITIVITY = {"low": "high", "mid": "medium", "high": "low"}  # low budget band = very price-sensitive
BUCKETS = ["small (<10%)", "medium (10–20%)", "large (>20%)"]
SENS_ORDER = ["high", "medium", "low"]
MIN_ROWS_FOR_CONCLUSION = 20  # fewer sends than this per cell = too noisy to conclude anything


def discount_bucket(pct: float) -> str:
    return BUCKETS[0] if pct < 10 else BUCKETS[1] if pct <= 20 else BUCKETS[2]


def build_send_table(db_path: str | Path | None = None) -> pd.DataFrame:
    """One row per delivered send: user, offer, discount %, bucket, sensitivity, redeemed (0/1)."""
    sends = store.load_sends(None, db_path)
    sends = sends[sends["status"].isin(DELIVERED)].copy()
    if sends.empty:
        return pd.DataFrame(columns=["user_id", "offer_id", "discount_pct", "bucket", "sensitivity", "redeemed"])
    offers = {o.offer_id: o for o in store.load_offers(db_path)}
    catalog = store.Catalog.load(db_path)
    sends = sends[sends["offer_id"].isin(offers)]
    sends["discount_pct"] = [discount_pct_equivalent(offers[o], catalog) for o in sends["offer_id"]]
    sends["bucket"] = sends["discount_pct"].map(discount_bucket)
    bands = {u: budget_band(u) for u in sends["user_id"].unique()}
    sends["sensitivity"] = sends["user_id"].map(lambda u: SENSITIVITY.get(bands[u], "medium"))
    sends["redeemed"] = (sends["status"] == "redeemed").astype(int)
    return sends[["user_id", "offer_id", "discount_pct", "bucket", "sensitivity", "redeemed"]]


def organic_rate(user_ids: list[str], db_path: str | Path | None = None) -> float | None:
    """Average chance these users place an order in any 7-day window (from the orders table)."""
    orders = read_table("orders", db_path)
    if orders.empty or not user_ids:
        return None
    dates = pd.to_datetime(orders["order_date"])
    weeks_total = max(1, (dates.max() - dates.min()).days // 7 + 1)
    orders["week"] = (dates - dates.min()).dt.days // 7
    weeks_with_order = orders.groupby("user_id")["week"].nunique()
    return float(weeks_with_order.reindex(user_ids).fillna(0).mean() / weeks_total)


def _pct(x: float | None) -> str:
    return "–" if x is None or pd.isna(x) else f"{100 * x:.0f}%"


def build_report(db_path: str | Path | None = None) -> str:
    table = build_send_table(db_path)
    lines = [
        "# Offer uplift analysis",
        "",
        f"_Generated {now_iso()} by `python -m offers.analysis` from `offer_sends` ({len(table)} delivered sends)._",
        "",
        "**Question:** do bigger discounts make people buy who otherwise wouldn't — or are we paying "
        "people who'd buy anyway?",
        "",
        "- *Redemption rate* = redeemed ÷ delivered (sent / opened / clicked / redeemed).",
        "- *Organic 7-day rate* = chance the same users order in any 7-day window without an offer.",
        "- *Uplift ≈ redemption − organic.* Near zero or negative ⇒ the discount mostly rewards a purchase "
        "that would have happened anyway.",
        "- Price sensitivity = behavioural budget band from the rec engine (low budget ⇒ high sensitivity).",
        "",
    ]
    if table.empty:
        lines += ["**No delivered offers yet** — run the pipeline (or load data with offer history) and re-run."]
        return "\n".join(lines) + "\n"

    lines += ["## Redemption rate (sends) by discount size × price sensitivity", "",
              "| Price sensitivity | " + " | ".join(BUCKETS) + " | Organic 7-day rate |",
              "|---|" + "---|" * (len(BUCKETS) + 1)]
    findings = []
    for sens in SENS_ORDER:
        group = table[table["sensitivity"] == sens]
        if group.empty:
            continue
        organic = organic_rate(list(group["user_id"].unique()), db_path)
        cells, rates = [], {}
        for b in BUCKETS:
            cell = group[group["bucket"] == b]
            rates[b] = (cell["redeemed"].mean() if len(cell) else None, len(cell))
            cells.append(f"{_pct(rates[b][0])} ({len(cell)})" if len(cell) else "–")
        lines.append(f"| {sens} | " + " | ".join(cells) + f" | {_pct(organic)} |")

        small, large = rates[BUCKETS[0]], rates[BUCKETS[2]]
        if small[0] is not None and large[0] is not None:
            enough = min(small[1], large[1]) >= MIN_ROWS_FOR_CONCLUSION
            gain = large[0] - small[0]
            note = "" if enough else " _(too few sends to be sure)_"
            if gain <= 0.02:
                findings.append(f"- **{sens} sensitivity:** large discounts redeem {_pct(large[0])} vs {_pct(small[0])} "
                                f"for small ones — the extra discount buys almost nothing. Keep discounts small.{note}")
            else:
                findings.append(f"- **{sens} sensitivity:** large discounts lift redemption by {100 * gain:.0f} points "
                                f"({_pct(small[0])} → {_pct(large[0])}) — discounts work here.{note}")
        overall = group["redeemed"].mean()
        if organic is not None and overall - organic <= 0.02:
            findings.append(f"- **{sens} sensitivity:** redemption ({_pct(overall)}) is not above the organic 7-day rate "
                            f"({_pct(organic)}) — no measurable uplift; offers here mostly reward purchases that "
                            f"would happen anyway.")
    lines += ["", "Cells show `rate (number of sends)`.", "", "## Findings", ""]
    lines += findings or ["- Not enough overlapping data to compare discount sizes yet (need sends in both the "
                          "small and large buckets for a sensitivity group)."]
    lines += ["", "## What the offer engine does about it", "",
              "- Discount = base % per trigger × sensitivity multiplier: **1.5× for price-sensitive, 1.0× medium, "
              "0.7× for low-sensitivity** users (`offers/rules.py`).",
              "- Never above `MAX_DISCOUNT_PCT` and never below a product's margin floor, whoever the user is.",
              "- Re-run this analysis when more history exists; if a group's uplift stays near zero, lower its multiplier.",
              ""]
    if len(table) < MIN_ROWS_FOR_CONCLUSION * 3:
        lines += ["> ⚠️ Small sample: with only a few dozen sends these numbers are anecdotes, not evidence. "
                  "The method is what matters; re-run on the full synthetic dataset (`DATA_SOURCE=synthetic`).", ""]
    return "\n".join(lines)


def write_report(output: str | Path = DEFAULT_OUTPUT, db_path: str | Path | None = None) -> Path:
    output = Path(output)
    output.write_text(build_report(db_path), encoding="utf-8")
    log.info("Wrote %s", output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Redemption rate by discount size x price sensitivity.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    print(f"Report written to {write_report(args.output)}")


if __name__ == "__main__":
    main()
