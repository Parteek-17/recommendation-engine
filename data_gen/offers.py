"""Step 6 — Offers, consent and simulated offer responses.

    offers  = build_offers(categories, products, relations, rng)    # ~60 starter offers
    consent = build_consent(users, hidden, rng)                      # ~60% opted in (never minors)
    OfferSimulator                                                    # used by simulate.py, day by day

The OfferSimulator plays the role of the future offer pipeline on PAST data: when a trigger
happens (cart abandoned, wishlisted item, consumable due, ...) it checks consent + weekly cap,
picks a matching offer and simulates the response: sent -> opened -> clicked -> redeemed.

Uplift is planted on purpose: 20% of eligible triggers are a HOLDOUT (nothing sent). Holdout
users still buy "anyway" at 20% of the treated redemption rate, so the true uplift of an offer
is ~80% of its redemptions — Phase 7 can measure that.
"""

from __future__ import annotations

import random
from datetime import datetime

import numpy as np
import pandas as pd

from data_gen import config as C
from data_gen.templates import COMPLEMENT_RULES, GIFT_SUBS, PARENTS
from data_gen.utils import day_index, seconds_to_iso

LIVE = (C.OFFERS_START + "T00:00:00", C.OFFERS_END)
DIWALI = ("2026-10-20T00:00:00", "2026-11-15T23:59:59")    # upcoming festival (pipeline testing)
PCT_STEPS = [5, 8, 10, 12, 15, 20, 25, 30, 35, 40]

# (code, offer_type, value, min_cart_value, segment, trigger_type, window, max_uses_per_user)
GENERIC_OFFERS = [
    ("CART10", "pct_off", 10, 499, "all", "cart_abandon", LIVE, 1),
    ("FREESHIP", "free_delivery", 0, 0, "tier3", "cart_abandon", LIVE, 1),
    ("CARTFLAT100", "flat_off", 100, 999, "all", "cart_abandon", LIVE, 1),
    ("CARTPOINTS50", "points", 50, 0, "all", "cart_abandon", LIVE, 1),
    ("NEWCART15", "pct_off", 15, 299, "new_users", "cart_abandon", LIVE, 1),
    ("DROP5", "pct_off", 5, 0, "all", "price_drop", LIVE, 1),
    ("DROPFLAT250", "flat_off", 250, 2499, "all", "price_drop", LIVE, 1),
    ("DROPPOINTS100", "points", 100, 0, "returning", "price_drop", LIVE, 1),
    ("BUYAGAIN5", "buy_again", 5, 0, "all", "repurchase_due", LIVE, 3),
    ("REORDER50", "flat_off", 50, 499, "all", "repurchase_due", LIVE, 2),
    ("REFILLSHIP", "free_delivery", 0, 0, "all", "repurchase_due", LIVE, 3),
    ("THANKYOU10", "pct_off", 10, 0, "all", "post_purchase", LIVE, 1),
    ("NEXTORDER75", "flat_off", 75, 599, "all", "post_purchase", LIVE, 1),
    ("REWARD100", "points", 100, 0, "all", "post_purchase", LIVE, 1),
    ("COMEBACK100", "flat_off", 100, 999, "all", "win_back", LIVE, 1),
    ("MISSYOU100", "points", 100, 0, "all", "win_back", LIVE, 1),
    ("WELCOMEBACK15", "pct_off", 15, 499, "returning", "win_back", LIVE, 1),
    ("WELCOMESHIP", "free_delivery", 0, 0, "new_users", "win_back", LIVE, 1),
    ("SALE10", "pct_off", 10, 0, "all", "festival", ("2026-08-10T00:00:00", "2026-08-15T23:59:59"), 1),
    ("DIWALI200", "points", 200, 0, "all", "festival", DIWALI, 1),
    ("DIWALIFLAT200", "flat_off", 200, 1499, "all", "festival", DIWALI, 1),
    ("FESTIVE1500", "flat_off", 1500, 9999, "all", "festival", LIVE, 1),        # big -> needs approval
]
# Deliberately broken offers so guardrail code always has something to block (like data/sample/).
GUARDRAIL_TEST_OFFERS = [
    # (code, offer_type, value, segment, trigger_type, window, is_active)
    ("MEGA50", "pct_off", 50, "all", "cart_abandon", LIVE, 1),                                  # > MAX_DISCOUNT_PCT
    ("SUMMER20", "pct_off", 20, "all", "festival", ("2026-05-01T00:00:00", "2026-05-31T23:59:59"), 1),  # expired
    ("OLDDROP10", "pct_off", 10, "all", "price_drop", LIVE, 0),                                 # inactive
]


def _row(code, otype, value, min_cart, segment, trigger, window, max_uses=1, category="", product="",
         bundle="", active=1) -> dict:
    return {"code": code, "offer_type": otype, "value": value, "min_cart_value": min_cart,
            "category_id": category, "product_id": product, "bundle_product_id": bundle, "segment": segment,
            "trigger_type": trigger, "max_uses_per_user": max_uses, "start_at": window[0], "expires_at": window[1],
            "is_active": active,
            "requires_approval": int(otype == "flat_off" and value >= C.APPROVAL_THRESHOLD_INR)}


def _discount_cap(category_id: str, categories: pd.DataFrame) -> int:
    """Biggest % discount a category can afford: the middle of its margin range (never more than 40%)."""
    parent = categories.set_index("category_id")["parent_id"].get(category_id, "")
    margins = C.PARENT_MARGINS.get(PARENTS.get(parent, ""), (15, 25))  # unknown categories: careful default
    return int(min(C.MAX_DISCOUNT_PCT, sum(margins) / 2))


def _short_code(name: str) -> str:
    letters = "".join(ch for ch in name.upper() if ch.isalpha())
    return letters[:8] or "CAT"


def build_offers(categories: pd.DataFrame, products: pd.DataFrame, relations: pd.DataFrame,
                 rng: np.random.Generator) -> pd.DataFrame:
    rows = [_row(*spec) for spec in GENERIC_OFFERS]

    # One offer per category, with a trigger that suits it.
    complement_kinds = {k for kinds in COMPLEMENT_RULES.values() for k in kinds}
    kind_col = products["_kind"] if "_kind" in products.columns else products["category_id"]
    names = dict(zip(categories["category_id"], categories["name"]))
    leaf_counts = products["category_id"].value_counts()
    leaves = sorted(leaf_counts.index, key=lambda c: (-leaf_counts[c], c))
    room = max(0, C.N_OFFERS_TARGET - len(GENERIC_OFFERS) - len(GUARDRAIL_TEST_OFFERS) - 4)
    other_triggers = ["price_drop", "cart_abandon"]
    for i, cat in enumerate(leaves[:room]):
        in_cat = products["category_id"] == cat
        cap = _discount_cap(cat, categories)
        value = int(rng.choice([v for v in PCT_STEPS if v <= cap]))
        if products.loc[in_cat, "is_consumable"].astype(int).mean() > 0.5:
            trigger = "repurchase_due"
        elif kind_col[in_cat].isin(complement_kinds).mean() > 0.5:
            trigger = "post_purchase"           # accessories: offered after buying the main item
        elif cat in GIFT_SUBS:
            trigger = "festival"
        else:
            trigger = other_triggers[i % 2]
        rows.append(_row(f"{_short_code(names.get(cat, cat))}{value}", "pct_off", value, 0, "all", trigger, LIVE,
                         category=cat))

    # Bundles: a popular main product + one of its complements ("buy together, save X%").
    comps = relations[relations["relation"] == "complement"]
    if len(comps):
        pop = products.set_index("product_id")["_pop"] if "_pop" in products.columns else None
        mains = comps["product_id"].drop_duplicates()
        if pop is not None:
            mains = mains.iloc[np.argsort(-pop.reindex(mains).to_numpy(), kind="stable")]
        for n, main in enumerate(mains.iloc[:4], start=1):
            partner = comps.loc[comps["product_id"] == main, "related_product_id"].iloc[0]
            value = int(min(15, _discount_cap(products.set_index("product_id").at[main, "category_id"],
                                              categories)))
            rows.append(_row(f"BUNDLE{n}{value}", "bundle", value, 0, "all", "post_purchase", LIVE,
                             product=main, bundle=partner))

    for code, otype, value, segment, trigger, window, active in GUARDRAIL_TEST_OFFERS:
        rows.append(_row(code, otype, value, 0, segment, trigger, window, active=active))

    offers = pd.DataFrame(rows)
    # Codes must be unique: add a number to repeats (e.g. two "SKINCARE10").
    seen: dict[str, int] = {}
    codes = []
    for code in offers["code"]:
        seen[code] = seen.get(code, 0) + 1
        codes.append(code if seen[code] == 1 else f"{code}{chr(ord('A') + seen[code] - 2)}")
    offers["code"] = codes
    offers.insert(0, "offer_id", [f"OF{i + 1:04d}" for i in range(len(offers))])
    return offers


def build_consent(users: pd.DataFrame, hidden: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Who agreed to receive offers. Minors are never opted in."""
    prefs = dict(zip(hidden["user_id"], hidden["preferred_categories"]))
    rows = []
    for uid, minor in zip(users["user_id"], users["is_minor"].astype(int)):
        opted = int(not minor and rng.random() < C.CONSENT_RATE)
        quiet = C.ALT_QUIET if rng.random() < C.ALT_QUIET_RATE else C.DEFAULT_QUIET
        stated = ""
        if opted and rng.random() < C.STATED_PREFS_RATE:
            own = prefs.get(uid, "").split("|")
            stated = "|".join(own[: int(rng.integers(1, 3))])
        cap = int(rng.integers(1, 3)) if rng.random() < C.LOWER_WEEKLY_CAP_RATE else C.MAX_OFFERS_PER_WEEK
        rows.append({"user_id": uid, "opted_in": opted, "quiet_start": quiet[0], "quiet_end": quiet[1],
                     "preferred_categories": stated, "max_offers_per_week": cap})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Simulated responses (used by simulate.py)
# ---------------------------------------------------------------------------
TRIGGER_PRIORITY = ["cart_abandon", "post_purchase", "repurchase_due", "price_drop", "festival", "win_back"]


def offer_message(offer: dict, title: str) -> str:
    """Plain template wording (what the pipeline sends when the LLM is unavailable)."""
    v, t = offer["value"], offer["offer_type"]
    deal = {
        "pct_off": f"Get {v:g}% off",
        "flat_off": f"Get ₹{v:g} off" + (f" on orders above ₹{offer['min_cart_value']:g}"
                                         if offer["min_cart_value"] else ""),
        "free_delivery": "Free delivery",
        "bundle": f"Buy together and save {v:g}%",
        "buy_again": f"Re-order and save {v:g}%",
        "points": f"Earn {v:g} bonus points",
    }[t]
    until = datetime.fromisoformat(offer["expires_at"]).strftime("%d %b %Y")
    return f"Hi! {deal} on {title}. Use code {offer['code']} - valid till {until}."


class OfferSimulator:
    """Decides, per trigger, whether an offer is sent and how the user responds."""

    def __init__(self, offers: pd.DataFrame, consent: pd.DataFrame, users: pd.DataFrame, hidden: pd.DataFrame,
                 products: pd.DataFrame, r: random.Random):
        self.r = r
        self.offers = []
        for o in offers.to_dict("records"):
            o["_start"] = day_index(o["start_at"])
            o["_end"] = day_index(o["expires_at"])
            usable = (o["is_active"] == 1 and o["requires_approval"] == 0
                      and not (o["offer_type"] == "pct_off" and o["value"] > C.MAX_DISCOUNT_PCT))
            if usable:  # the simulated pipeline obeys the same guardrails as the real one will
                self.offers.append(o)
        self.first_day = day_index(C.OFFERS_START)
        con = consent.set_index("user_id")
        self.opted = con.loc[users["user_id"], "opted_in"].astype(int).to_numpy()
        self.week_cap = con.loc[users["user_id"], "max_offers_per_week"].astype(int).to_numpy()
        self.tier = users["city_tier"].astype(int).to_numpy()
        self.signup_day = np.array([day_index(s) for s in users["signup_date"]])
        self.user_ids = users["user_id"].tolist()
        self.sens = hidden["price_sensitivity"].astype(float).to_numpy()
        self.product_ids = products["product_id"].tolist()
        self.titles = products["title"].tolist()
        self.price = products["price"].astype(float).to_numpy()
        self.cat = products["category_id"].tolist()
        self.pid_index = {p: i for i, p in enumerate(self.product_ids)}
        self.sent_days: dict[int, list[int]] = {}
        self.uses: dict[tuple[int, str], int] = {}
        self.rows: list[dict] = []

    # -- matching -----------------------------------------------------------------------------
    def _segment_ok(self, seg: str, u: int, day: int, orders: int) -> bool:
        if seg == "tier3":
            return self.tier[u] == 3
        if seg == "new_users":
            return day - self.signup_day[u] <= 30
        if seg == "returning":   # same rule as offers/segments.py: has ordered before
            return orders >= 1
        return True

    def match(self, trigger: str, u: int, day: int, anchor: int, target: int, orders: int) -> tuple[dict, int] | None:
        """Best usable offer for this trigger (product-specific > category > generic) and the product to buy."""
        best: list[tuple[int, dict, int]] = []
        for o in self.offers:
            if o["trigger_type"] != trigger or not (o["_start"] <= day <= o["_end"]):
                continue
            if self.uses.get((u, o["offer_id"]), 0) >= o["max_uses_per_user"]:
                continue
            if not self._segment_ok(o["segment"], u, day, orders):
                continue
            goal = target
            if o["offer_type"] == "bundle":
                if anchor < 0 or self.product_ids[anchor] != o["product_id"]:
                    continue
                goal, level = self.pid_index[o["bundle_product_id"]], 3
            elif o["product_id"]:
                if target < 0 or self.product_ids[target] != o["product_id"]:
                    continue
                level = 3
            elif o["category_id"]:
                if target < 0 or self.cat[target] != o["category_id"]:
                    continue
                level = 2
            else:
                level = 1
            if goal < 0 or self.price[goal] < float(o["min_cart_value"] or 0):
                continue
            best.append((level, o, goal))
        if not best:
            return None
        top = max(level for level, _, _ in best)
        _, offer, goal = self.r.choice([b for b in best if b[0] == top])
        return offer, goal

    def discount_fraction(self, offer: dict, product: int) -> float:
        v, t, price = float(offer["value"]), offer["offer_type"], max(1.0, self.price[product])
        if t in ("pct_off", "bundle", "buy_again"):
            return v / 100
        if t == "flat_off":
            return min(1.0, v / price)
        if t == "points":
            return min(0.10, v * 0.25 / price)      # 1 point ≈ ₹0.25
        return 0.05                                  # free delivery ≈ 5% of a small order

    def price_paid(self, offer: dict | None, product: int) -> float:
        price = self.price[product]
        if offer is None or offer["offer_type"] in ("free_delivery", "points"):
            return round(price, 2)
        if offer["offer_type"] == "flat_off":
            return round(max(0.0, price - float(offer["value"])), 2)
        return round(price * (1 - float(offer["value"]) / 100), 2)

    # -- the daily decision ---------------------------------------------------------------------
    def process(self, day: int, triggers: list[tuple[int, str, int, int]], orders: np.ndarray,
                prefers_category) -> list[dict]:
        """triggers: (user, trigger_type, anchor product or -1, target product or -1).

        Returns actions for simulate.py: {"kind": "offer" | "organic", "user", "product",
        "time" (seconds), "offer" (dict or None), "redeem" (bool)}.
        """
        if day < self.first_day:
            return []
        actions: list[dict] = []
        done_today: set[int] = set()
        for u, trigger, anchor, target in sorted(triggers, key=lambda x: (x[0], TRIGGER_PRIORITY.index(x[1]))):
            if u in done_today or not self.opted[u]:
                continue
            done_today.add(u)
            found = self.match(trigger, u, day, anchor, target, int(orders[u]))
            if found is None:
                continue
            offer, goal = found
            send_sec = day * 86400 + self.r.randint(C.SEND_HOURS[0] * 3600, C.SEND_HOURS[1] * 3600)
            recent = [d for d in self.sent_days.get(u, []) if d > day - 7]
            p_redeem = min(0.95, C.REDEEM_BASE * (1 + C.REDEEM_DISCOUNT_FACTOR * self.discount_fraction(offer, goal)
                                                  * self.sens[u]) * (C.REDEEM_TASTE_MULT
                                                                     if prefers_category(u, goal) else 1.0))
            if len(recent) >= self.week_cap[u]:
                self._record(u, offer, trigger, goal, "blocked", None, send_sec)
                continue
            if self.r.random() < C.HOLDOUT_RATE:
                # Control group: nothing sent. Some buy anyway (makes uplift measurable).
                p_total = (1 - C.FAILED_RATE) * C.OPEN_RATE * C.CLICK_OF_OPENED * p_redeem
                if self.r.random() < C.WOULD_BUY_ANYWAY * p_total:
                    actions.append({"kind": "organic", "user": u, "product": goal,
                                    "time": send_sec + self.r.randint(1800, 6 * 3600), "offer": None,
                                    "redeem": False})
                continue
            self.sent_days.setdefault(u, []).append(day)
            if self.r.random() < C.FAILED_RATE:
                self._record(u, offer, trigger, goal, "failed", send_sec, send_sec + 60)
                continue
            status, t = "sent", send_sec
            if self.r.random() < C.OPEN_RATE:
                status, t = "opened", t + self.r.randint(60, 3 * 3600)
                if self.r.random() < C.CLICK_OF_OPENED:
                    status, t = "clicked", t + self.r.randint(10, 1800)
                    redeem = self.r.random() < p_redeem
                    actions.append({"kind": "offer", "user": u, "product": goal, "time": t, "offer": offer,
                                    "redeem": redeem})
                    if redeem:
                        status, t = "redeemed", t + self.r.randint(120, 3600)
                        self.uses[(u, offer["offer_id"])] = self.uses.get((u, offer["offer_id"]), 0) + 1
            self._record(u, offer, trigger, goal, status, send_sec, t)
        return actions

    def _record(self, u: int, offer: dict, trigger: str, product: int, status: str, sent: int | None,
                updated: int) -> None:
        self.rows.append({"user_id": self.user_ids[u], "offer_id": offer["offer_id"], "trigger_type": trigger,
                          "message": offer_message(offer, self.titles[product]), "channel": "telegram",
                          "status": status, "_sent": sent, "_updated": updated})

    def sends_table(self, window_seconds: int) -> pd.DataFrame:
        cols = ["send_id", "user_id", "offer_id", "trigger_type", "message", "channel", "status", "sent_at",
                "updated_at"]
        if not self.rows:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(self.rows)
        df["_updated"] = df["_updated"].clip(upper=window_seconds - 1)
        df = df.sort_values(["_updated"], kind="stable").reset_index(drop=True)
        sent = df["_sent"]
        df["sent_at"] = seconds_to_iso(sent.fillna(0)).where(sent.notna().to_numpy(), "")
        df["updated_at"] = seconds_to_iso(df["_updated"])
        df["send_id"] = [f"SN{i + 1:06d}" for i in range(len(df))]
        return df[cols]
