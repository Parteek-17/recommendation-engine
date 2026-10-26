"""Step 9 — Re-ranking rules: business logic on top of the ranker's ordering.

Models predict what a user might click; rules make sure the list is also SENSIBLE:
  1. Remove out-of-stock products.
  2. Hide age-restricted products from minors.
  3. Post-purchase intelligence: never recommend a non-consumable the user already
     bought, nor (for 60 days) its SUBSTITUTES (bought a phone -> no more phones).
     Substitute = same leaf category + same kind (from the title: "smartphone", "cover")
     + within 4× price, so owning earbuds no longer hides phone covers and chargers.
     Right after a purchase (14 days) the WHOLE leaf category is hidden, whatever the
     price or kind (only complements of that purchase stay): someone who just bought a
     ₹46k phone doesn't want a ₹1.8k keypad phone either...
  4. ...but DO boost COMPLEMENTS of recent purchases (phone -> cover, charger) — much
     harder in the first week, so they actually reach the top of the list —
     and remind about consumables that are due for a refill ("Time to restock").
  5. Respect the budget: strict filter if the user picked a budget band, otherwise
     gently push down products far above their usual price level.
  6. Variety: at most 3 products per brand and per category per 10 items in a list.
  7. Long tail: ~20% of slots go to less-popular products, so the store doesn't
     only ever show its 20 bestsellers (helps discovery + fairness to sellers).
     Not for users with no history yet — they get the in-stock bestsellers.
  8. Gift mode: personal history is ignored (the engine passes an empty history).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pandas as pd

from reco import explain
from reco.data import UserHistory, isin

if TYPE_CHECKING:
    from reco.engine import Recommender, Taste

MAX_PER_BRAND = 3
MAX_PER_CATEGORY = 3
LONG_TAIL_SHARE = 0.2
RECENT_PURCHASE_DAYS = 30       # complements are suggested for purchases this recent
SUBSTITUTE_BLOCK_DAYS = 60      # after buying a phone, hide other phones for this long
SAME_CATEGORY_BLOCK_DAYS = 14   # just bought: hide the whole leaf category, ignoring the 4× price rule
FRESH_PURCHASE_DAYS = 7         # "just bought" -> complements get the stronger boost below
RESTOCK_EARLY_FACTOR = 0.8      # remind when 80% of the usual repurchase cycle has passed
PRICE_GAP_PENALTY = 0.4         # this much above the user's usual price position -> pushed down
COMPLEMENT_BOOST = (0.25, 0.25)  # base + strength × relation score (scores are 0..1 at this point)
FRESH_COMPLEMENT_BOOST = (0.6, 0.4)  # same, for purchases in the last FRESH_PURCHASE_DAYS days
RESTOCK_BOOST = 0.6
# explicit budget band (user control) -> highest allowed price position inside a category
BUDGET_LIMITS = {"low": 0.4, "mid": 0.75, "high": 1.0}
# Rule names, so evaluation can switch one off at a time and measure what it costs/gains.
RULES = ("stock", "minors", "owned", "substitutes", "complements_restock", "budget", "diversity", "long_tail")


def _days_between(earlier: str, later: str) -> float:
    try:
        return (pd.to_datetime(later) - pd.to_datetime(earlier)).total_seconds() / 86400
    except (ValueError, TypeError):
        return math.inf


def _post_purchase_extras(history: UserHistory, engine: Recommender) -> list[dict]:
    """Complements of recent purchases + restock reminders for consumables."""
    rows = []
    now = history.now or engine.ref_time
    items = engine.items
    for pid, bought_at in history.purchases.items():
        if pid not in items.index:
            continue
        age = _days_between(bought_at, now)
        consumable = items.at[pid, "is_consumable"] == 1
        cycle = items.at[pid, "repurchase_days"]
        if consumable and pd.notna(cycle) and age >= RESTOCK_EARLY_FACTOR * float(cycle):
            rows.append({"product_id": pid, "boost": RESTOCK_BOOST, "reason_type": "restock", "anchor": pid})
        if age <= RECENT_PURCHASE_DAYS and not consumable:
            base, strength = FRESH_COMPLEMENT_BOOST if age <= FRESH_PURCHASE_DAYS else COMPLEMENT_BOOST
            for comp in engine.complements(pid, k=6):
                rows.append({"product_id": comp.product_id, "boost": base + strength * comp.score,
                             "reason_type": "complement", "anchor": pid})
    return rows


def rerank(cands: pd.DataFrame, history: UserHistory, taste: Taste, engine: Recommender, k: int,
           context: dict, is_minor: bool = False, disabled: frozenset[str] = frozenset()) -> pd.DataFrame:
    """Apply the rules and return the final top-k (product_id, score, source, reason_type, anchor).

    disabled: rule names from RULES to skip (evaluation only — production uses all rules).
    """
    items = engine.items
    df = pd.DataFrame({
        "product_id": cands["product_id"],
        "score": cands["score"].astype(float),
        "source": cands["best_source"],
        "reason_type": cands["best_source"],
        "anchor": [explain.anchor_of(r) for r in cands.to_dict("records")],
    })
    if len(df):  # scores to 0..1 so the boosts below mean the same for every user
        lo, hi = df["score"].min(), df["score"].max()
        df["score"] = (df["score"] - lo) / (hi - lo) if hi > lo else 1.0

    # --- 4. post-purchase extras (personal; skipped in gift mode because history is empty)
    on = lambda rule: rule not in disabled  # noqa: E731
    extras = pd.DataFrame(_post_purchase_extras(history, engine) if on("complements_restock") else [])
    if not extras.empty:
        extras = extras.sort_values("boost", ascending=False).drop_duplicates("product_id")
        present = isin(df["product_id"], set(extras["product_id"]))
        boost = df["product_id"].map(extras.set_index("product_id")["boost"])
        df.loc[present, "score"] += boost[present]
        info = extras.set_index("product_id")
        df.loc[present, "reason_type"] = df.loc[present, "product_id"].map(info["reason_type"])
        df.loc[present, "anchor"] = df.loc[present, "product_id"].map(info["anchor"])
        df.loc[present, "source"] = df.loc[present, "reason_type"]
        new = extras[~isin(extras["product_id"], set(df["product_id"]))]
        df = pd.concat([df, pd.DataFrame({"product_id": new["product_id"], "score": new["boost"],
                                          "source": new["reason_type"], "reason_type": new["reason_type"],
                                          "anchor": new["anchor"]})], ignore_index=True)

    # --- 1. stock, unknown products
    df = df[isin(df["product_id"], engine.item_set)]
    if on("stock"):
        df = df[engine.in_stock(df["product_id"]).values]

    # --- 2. minors
    if is_minor and on("minors"):
        df = df[(items["age_restricted"].reindex(df["product_id"]).fillna(0) != 1).values]

    # --- 3. already-bought non-consumables (forever) and their substitutes (for a while)
    now = history.now or engine.ref_time
    owned = [p for p in history.purchases if p in items.index and items.at[p, "is_consumable"] != 1]
    if owned and on("owned"):
        blocked = isin(df["product_id"], set(owned))
        recent = [p for p in owned if _days_between(history.purchases[p], now) <= SUBSTITUTE_BLOCK_DAYS]
        if recent and on("substitutes"):
            blocked |= isin(df["product_id"], set().union(*(engine.substitutes_of(o) for o in recent)))
            # Just bought (14 days): the WHOLE leaf category, at any price and of any kind (no keypad
            # phone after a flagship) — except complements of those fresh purchases (phone + cover
            # bought together must not hide the charger that is also in "Mobile Accessories").
            fresh = {p for p in recent if _days_between(history.purchases[p], now) <= SAME_CATEGORY_BLOCK_DAYS}
            fresh_cats = {c for c in items["category_id"].reindex(list(fresh)) if isinstance(c, str)}
            if fresh_cats:
                goes_with = set(extras.loc[isin(extras["anchor"], fresh) & (extras["reason_type"] == "complement"),
                                           "product_id"]) if not extras.empty else set()
                blocked |= (isin(items["category_id"].reindex(df["product_id"]), fresh_cats)
                            & ~isin(df["product_id"], goes_with))
        df = df[~blocked | (df["reason_type"] == "restock")]

    # --- 5. budget
    band = context.get("budget_band")
    price_pct = items["price_pct"].reindex(df["product_id"]).astype(float)
    if band in BUDGET_LIMITS:  # the user chose a budget -> hard rule (always on: it's the user's choice)
        df = df[(price_pct.isna() | (price_pct <= BUDGET_LIMITS[band])).values]
    elif taste.price_pos is not None and on("budget"):
        too_pricey = ((price_pct - taste.price_pos) > PRICE_GAP_PENALTY).values
        df.loc[too_pricey, "score"] *= 0.6

    # --- gift mode: optional recipient categories from the user
    gift_cats = context.get("top_categories") if context.get("is_gift_mode") else None
    if gift_cats:
        cats = items.reindex(df["product_id"])["category_id"]
        df.loc[isin(cats, set(gift_cats)), "score"] += 0.5
    if context.get("is_gift_mode"):
        df["reason_type"] = "gift"

    df = df.sort_values("score", ascending=False)
    # No long-tail slots for users we know nothing about (new users, gift mode): they mostly click
    # bestsellers, and discovery works better once we know their taste. (Tuned on the validation window.)
    long_tail = on("long_tail") and not history.is_cold
    return _diverse_top_k(df, items, k, caps=on("diversity"), long_tail=long_tail)


def _diverse_top_k(df: pd.DataFrame, items: pd.DataFrame, k: int, caps: bool = True,
                   long_tail: bool = True) -> pd.DataFrame:
    """Greedy pick in score order with brand/category caps and reserved long-tail slots (6 + 7)."""
    rows = df.to_dict("records")
    longtail = items["is_longtail"].to_dict()
    # caps are "per 10 items": a list of 40 may hold 12 per category
    max_brand = math.ceil(MAX_PER_BRAND * max(k, 10) / 10)
    max_cat = math.ceil(MAX_PER_CATEGORY * max(k, 10) / 10)
    lt_available = sum(1 for r in rows if longtail.get(r["product_id"], True))
    lt_needed = min(math.ceil(LONG_TAIL_SHARE * k), lt_available) if long_tail else 0

    def pick(caps: bool, lt_needed: int = lt_needed) -> list[dict]:
        chosen, brands, cats, head_count = [], {}, {}, 0
        for r in rows:
            if len(chosen) == k:
                break
            pid = r["product_id"]
            brand, cat = items.at[pid, "brand"], items.at[pid, "category_id"]
            is_lt = longtail.get(pid, True)
            if caps:
                if isinstance(brand, str) and brands.get(brand, 0) >= max_brand:
                    continue
                if isinstance(cat, str) and cats.get(cat, 0) >= max_cat:
                    continue
            if not is_lt and head_count >= k - lt_needed:
                continue  # keep the reserved slots for long-tail products
            chosen.append(r)
            head_count += not is_lt
            if isinstance(brand, str):
                brands[brand] = brands.get(brand, 0) + 1
            if isinstance(cat, str):
                cats[cat] = cats.get(cat, 0) + 1
        return chosen

    chosen = pick(caps=caps)
    if len(chosen) < k and lt_needed:  # long-tail slots unfillable within the caps -> drop the reservation first
        have = {r["product_id"] for r in chosen}
        chosen += [r for r in pick(caps=caps, lt_needed=0) if r["product_id"] not in have][: k - len(chosen)]
    if len(chosen) < k:  # caps too strict for a small catalog -> fill the rest without them
        have = {r["product_id"] for r in chosen}
        chosen += [r for r in pick(caps=False) if r["product_id"] not in have][: k - len(chosen)]
    if len(chosen) < k:  # still short (e.g. long-tail reservation unfillable) -> plain score order
        have = {r["product_id"] for r in chosen}
        chosen += [r for r in rows if r["product_id"] not in have][: k - len(chosen)]
    out = pd.DataFrame(chosen, columns=["product_id", "score", "source", "reason_type", "anchor"])
    return out.sort_values("score", ascending=False).reset_index(drop=True)
