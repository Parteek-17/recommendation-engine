"""Concrete examples for docs/REPORT.md: post-purchase switch and cold start.

    python -m pipeline.report_examples      # -> pipeline/report_examples.md

Runs on a TEMPORARY COPY of app.db (we add one purchase to show the before/after).
Ground truth (product_relations) is used only to label the examples.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pandas as pd

from common.config import PROJECT_ROOT, settings
from common.db import get_connection, read_ground_truth

OUT = PROJECT_ROOT / "pipeline" / "report_examples.md"
NOW = "2026-09-30T20:00:00"


def _rows(recs, titles, cats, label=lambda pid: "") -> list[str]:
    return [f"| {i} | {titles.get(r.product_id, r.product_id)} | {cats.get(r.product_id, '')} | {r.source} | "
            f"{r.reason} | {label(r.product_id)} |" for i, r in enumerate(recs, 1)]


def main() -> None:
    import reco

    tmp = Path(tempfile.mkdtemp(prefix="report_examples_"))
    shutil.copy(settings.DB_PATH, tmp / "copy.db")
    settings.DB_PATH = tmp / "copy.db"

    with get_connection() as conn:
        products = pd.read_sql_query("SELECT p.product_id, p.title, p.price, p.launch_date, c.name AS cat, p.category_id "
                                     "FROM products p JOIN categories c ON c.category_id = p.category_id", conn)
        users = pd.read_sql_query("SELECT u.user_id, u.signup_date, COUNT(e.event_id) AS n FROM users u "
                                  "LEFT JOIN events e ON e.user_id = u.user_id GROUP BY u.user_id", conn)
        interacted = {r[0] for r in conn.execute("SELECT DISTINCT product_id FROM events")}
    titles = dict(zip(products["product_id"], products["title"]))
    cats = dict(zip(products["product_id"], products["cat"]))
    rel = read_ground_truth("product_relations")
    comp = set(zip(rel.loc[rel.relation == "complement", "product_id"], rel.loc[rel.relation == "complement", "related_product_id"]))
    subs = set(zip(rel.loc[rel.relation == "substitute", "product_id"], rel.loc[rel.relation == "substitute", "related_product_id"]))

    out = ["# Report examples (`python -m pipeline.report_examples`)", ""]

    # --- 1. Post-purchase switch -------------------------------------------------------------
    # A popular phone that has ground-truth complements (covers, chargers...).
    with get_connection() as conn:
        popular = [r[0] for r in conn.execute(
            "SELECT e.product_id FROM events e JOIN products p ON p.product_id = e.product_id "
            "JOIN categories c ON c.category_id = p.category_id WHERE c.name = 'Mobiles' "
            "GROUP BY e.product_id ORDER BY COUNT(*) DESC LIMIT 20")]
    pid = next(p for p in popular if any(a == p for a, _ in comp))
    phone = products.set_index("product_id").loc[pid]
    with get_connection() as conn:
        viewers = [r[0] for r in conn.execute(
            "SELECT user_id FROM events WHERE product_id = ? AND event_type IN ('view','click') "
            "AND user_id NOT IN (SELECT user_id FROM events WHERE event_type = 'purchase' AND product_id = ?) "
            "GROUP BY user_id ORDER BY COUNT(*) DESC LIMIT 1", (pid, pid))]
    user = viewers[0]
    label = lambda p: "true complement" if (pid, p) in comp else ("**substitute!**" if (pid, p) in subs else "")
    before = reco.get_recommendations(user, k=8)
    with get_connection() as conn:
        conn.execute("INSERT INTO events (event_id, user_id, product_id, session_id, event_type, timestamp, source) "
                     "VALUES ('E99999999', ?, ?, 'S9999999', 'purchase', ?, 'product_page')", (user, pid, NOW))
    after = reco.get_recommendations(user, k=8)
    n_sub_before = sum((pid, r.product_id) in subs for r in before)
    n_sub_after = sum((pid, r.product_id) in subs for r in after)
    n_comp_after = sum((pid, r.product_id) in comp for r in after)
    out += [f"## Post-purchase switch: {user} buys “{phone['title']}” (₹{phone['price']:,.0f})", "",
            f"Before the purchase {user} had been looking at this phone. Ground-truth labels are relative to the phone.", "",
            "**Before** (still shopping for a phone)", "",
            "| # | Product | Category | Source | Reason shown | Ground truth |", "|---|---|---|---|---|---|"]
    out += _rows(before, titles, cats, label)
    out += ["", "**After** buying it", "", "| # | Product | Category | Source | Reason shown | Ground truth |",
            "|---|---|---|---|---|---|"]
    out += _rows(after, titles, cats, label)
    out += ["", f"Substitutes of the phone in the top 8: **{n_sub_before} before → {n_sub_after} after**. "
                f"True complements after: **{n_comp_after}**.", ""]

    # What the storefront shows: reco + the API's post-purchase layer (api/personalize.py).
    from api import personalize

    personalize.clear_cache()
    shown = personalize.recommend(user, k=8)
    same_cat = sum(c["category_id"] == phone["category_id"] for c in shown)
    out += ["**After, as shown in the storefront** (`GET /recommend`: reco + the API's post-purchase switch)", "",
            "| # | Product | Category | Source | Reason shown | Ground truth |", "|---|---|---|---|---|---|"]
    out += [f"| {i} | {c['title']} | {cats.get(c['product_id'], '')} | {c['source']} | {c['reason']} | "
            f"{label(c['product_id'])} |" for i, c in enumerate(shown, 1)]
    comps = reco.get_complements(pid, k=5)
    out += ["", f"Other phones in the storefront list: **{same_cat}**; true complements: "
                f"**{sum((pid, c['product_id']) in comp for c in shown)}**.", "",
            f"`get_complements` for the phone: " + "; ".join(
                f"{titles.get(r.product_id, r.product_id)} ({label(r.product_id) or 'not in ground truth'})" for r in comps), ""]

    # --- 2. Cold-start user -------------------------------------------------------------------
    cold_user = users[users["n"] == 0].sort_values("signup_date").iloc[-1]["user_id"]
    recs = reco.get_recommendations(cold_user, k=6)
    out += [f"## Cold-start user: {cold_user} (signed up, no events yet)", "",
            "| # | Product | Category | Source | Reason shown | |", "|---|---|---|---|---|---|"]
    out += _rows(recs, titles, cats)

    # --- 3. Cold-start product ------------------------------------------------------------------
    new = products[~products["product_id"].isin(interacted)].sort_values("launch_date").iloc[-1]
    sim = reco.get_similar(new["product_id"], k=5)
    out += ["", f"## Cold-start product: “{new['title']}” (launched {new['launch_date']}, zero interactions)", "",
            "Collaborative filtering knows nothing about it; the content model (TF-IDF on title/brand/category/tags) still finds alternatives:", "",
            "| # | Similar product | Category | Source | Reason shown | |", "|---|---|---|---|---|---|"]
    out += _rows(sim, titles, cats)

    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
