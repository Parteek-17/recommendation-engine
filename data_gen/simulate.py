"""Step 4 — Behaviour simulation, day by day.

    result = Simulator(products, categories, users, hidden, relations, offers, consent, scale, rng).run()

Every day, every signed-up user may start sessions (more on weekends and in sales). In a
session they browse (80% in their hidden preferred categories), maybe search, view products
(long-tail popularity), and move down the funnel: view -> click -> add_to_cart -> purchase.

Planted patterns (what our models should discover):
  * taste + price comfort raise every funnel rate; high ratings help a little
  * after buying a non-consumable: substitutes (and the item itself) lose interest for 60 days,
    complements gain interest for 14 days, 25% come back to buy a complement
  * consumables are re-bought around repurchase_days ± 20% (60% of the time)
  * sale window = more traffic + more conversion; 10 "viral" products for 2-3 weeks
  * gift sessions (is_gift=1) browse outside the user's usual categories
  * offers (from July): triggers -> OfferSimulator -> telegram sessions / redemptions
  * minors never see age-restricted products

Output: plain lists turned into DataFrames with final ids (S..., E...) sorted by time.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from data_gen import config as C
from data_gen.offers import OfferSimulator
from data_gen.templates import GIFT_SUBS, SEARCH_PATTERNS
from data_gen.utils import N_DAYS, day_index, day_to_date, seconds_to_iso

EVENT_TYPES = ["view", "click", "add_to_cart", "remove_from_cart", "wishlist", "purchase", "search"]
SOURCES = ["home", "search", "product_page", "recommendation", "telegram"]
VIEW, CLICK, CART, REMOVE, WISH, BUY, SEARCH = range(7)
HOME, SRC_SEARCH, PAGE, RECO, TELEGRAM = range(5)
WINDOW_SECONDS = N_DAYS * 86400


@dataclass
class UserState:
    """What the simulation remembers about one user while the days go by."""

    prefs: list[int]                    # preferred sub-category indexes
    pref_w: list[float]
    comfort: float                      # preferred price percentile inside a category (0 cheap .. 1 premium)
    minor: bool
    gifter: bool
    signup_day: int
    last_session: int = -999           # day of the latest session
    last_order_sid: int = -1           # session of the latest order (one order per session)
    last_winback: int = -999
    # sub index -> {product index: until_day}; interest multiplier while active
    blocks: dict[int, dict[int, int]] = field(default_factory=dict)
    boosts: dict[int, dict[int, int]] = field(default_factory=dict)
    boost_list: list[tuple[int, int]] = field(default_factory=list)   # (product, until_day) for nudges
    last_bought: dict[int, int] = field(default_factory=dict)          # product -> day


@dataclass
class SimResult:
    sessions: pd.DataFrame
    events: pd.DataFrame
    purchases: pd.DataFrame   # session_id, user_id, product_id, timestamp, quantity, price_paid
    offer_sends: pd.DataFrame


class Simulator:
    def __init__(self, products: pd.DataFrame, categories: pd.DataFrame, users: pd.DataFrame,
                 hidden: pd.DataFrame, relations: pd.DataFrame, offers: pd.DataFrame, consent: pd.DataFrame,
                 activity_scale: float, rng: np.random.Generator):
        self.rng = rng                                            # numpy: vector draws
        self.r = random.Random(int(rng.integers(2**31)))          # python: fast single draws
        self.products, self.users = products, users
        self._prepare_products(products)
        self._prepare_users(users, hidden, activity_scale)
        self._prepare_relations(relations)
        self._prepare_viral()
        self.offer_sim = OfferSimulator(offers, consent, users, hidden, products, self.r)
        self.order_count = np.zeros(self.n_users, dtype=int)   # for the "returning" offer segment

        # Output buffers (plain lists are much faster than growing DataFrames).
        self.s_user: list[int] = []
        self.s_start: list[int] = []
        self.s_device: list[str] = []
        self.s_gift: list[int] = []
        self.e_user: list[int] = []
        self.e_prod: list[int] = []
        self.e_sess: list[int] = []
        self.e_type: list[int] = []
        self.e_time: list[int] = []
        self.e_src: list[int] = []
        self.e_query: list[str] = []
        self.purchases: list[tuple] = []   # (session, user, product, time, qty, price_paid)

        # Scheduled sessions and offer triggers, keyed by day.
        self.scheduled: dict[int, list[tuple]] = {}
        self.due_triggers: dict[int, list[tuple[int, int]]] = {}
        self.today_carts: list[tuple[int, int]] = []
        self.today_wishes: list[tuple[int, int]] = []
        self.today_bought: list[tuple[int, int]] = []
        self._t = 0   # clock of the session being built (seconds since WINDOW_START)

    # ------------------------------------------------------------------ setup
    def _prepare_products(self, p: pd.DataFrame) -> None:
        self.n_products = len(p)
        self.price = p["price"].astype(float).to_numpy()
        self.pop = (p["_pop"] if "_pop" in p.columns else pd.Series(np.ones(len(p)))).astype(float).to_numpy()
        self.rating = pd.to_numeric(p["avg_rating"], errors="coerce").fillna(4.0).to_numpy()
        self.launch_day = np.array([day_index(d) if isinstance(d, str) and d else -1 for d in p["launch_date"]])
        self.stock_ok = pd.to_numeric(p["stock_qty"], errors="coerce").fillna(100).to_numpy() > 0
        self.adult = p["age_restricted"].astype(int).to_numpy().astype(bool)
        self.consumable = p["is_consumable"].astype(int).to_numpy().astype(bool)
        self.rep_days = pd.to_numeric(p["repurchase_days"], errors="coerce").fillna(30).astype(int).to_numpy()
        self.brand = p["brand"].fillna("").astype(str).tolist()
        self.query = (p["_query"] if "_query" in p.columns else p["category_id"]).astype(str).tolist()

        self.subs = sorted(p["category_id"].unique())
        self.sub_of_cat = {s: i for i, s in enumerate(self.subs)}
        self.sub = np.array([self.sub_of_cat[c] for c in p["category_id"]])
        self.sub_products = [np.flatnonzero(self.sub == i) for i in range(len(self.subs))]
        # Price percentile of each product inside its own category (0 = cheapest, 1 = priciest).
        self.price_q = np.zeros(self.n_products)
        for idx in self.sub_products:
            ranks = self.price[idx].argsort(kind="stable").argsort(kind="stable")
            self.price_q[idx] = ranks / max(1, len(idx) - 1)
        counts = np.array([len(i) for i in self.sub_products], float)
        self.sub_weights = counts / counts.sum()   # exploration: bigger categories get more browsing
        gift = [self.sub_of_cat[s] for s in GIFT_SUBS if s in self.sub_of_cat]
        self.gift_subs = gift or list(range(len(self.subs)))

    def _prepare_users(self, users: pd.DataFrame, hidden: pd.DataFrame, scale: float) -> None:
        h = hidden.set_index("user_id").loc[users["user_id"]]
        self.n_users = len(users)
        self.user_ids = users["user_id"].tolist()
        self.device = users["device"].fillna("mobile").astype(str).tolist()
        self.state: list[UserState] = []
        for i, (_, row) in enumerate(h.iterrows()):
            prefs = [self.sub_of_cat[c] for c in str(row["preferred_categories"]).split("|") if c in self.sub_of_cat]
            weights = list(row["_pref_weights"])[: len(prefs)] if prefs else []
            comfort = float(np.clip(1 - float(row["price_sensitivity"]) + self.r.uniform(-0.1, 0.1), 0, 1))
            self.state.append(UserState(
                prefs=prefs or [0], pref_w=weights or [1.0], comfort=comfort,
                minor=bool(int(users["is_minor"].iloc[i])), gifter=bool(int(row["is_gifter"])),
                signup_day=day_index(users["signup_date"].iloc[i])))
        self.rate = h["_sessions_per_month"].astype(float).to_numpy() / 30.4 * scale
        self.signup_day = np.array([s.signup_day for s in self.state])

    def _prepare_relations(self, rel: pd.DataFrame) -> None:
        index = {pid: i for i, pid in enumerate(self.products["product_id"])}
        self.subst: dict[int, list[int]] = {}
        self.comps: dict[int, list[int]] = {}
        for a, b, kind in zip(rel["product_id"], rel["related_product_id"], rel["relation"]):
            if a in index and b in index:
                (self.subst if kind == "substitute" else self.comps).setdefault(index[a], []).append(index[b])

    def _prepare_viral(self) -> None:
        """10 products suddenly become popular for 2-3 weeks."""
        candidates = np.flatnonzero((self.launch_day <= 0) & ~self.adult)
        n = min(C.VIRAL_PRODUCTS, len(candidates))
        picks = self.rng.choice(candidates, size=n, replace=False) if n else []
        self.viral: list[tuple[int, int, int]] = []
        for p in picks:
            length = int(self.rng.integers(C.VIRAL_DAYS[0], C.VIRAL_DAYS[1] + 1))
            start = int(self.rng.integers(0, max(1, N_DAYS - length)))
            self.viral.append((int(p), start, start + length))

    # ------------------------------------------------------------------ helpers
    def _prepare_day(self, d: int) -> None:
        """Per-category product weights for today: popularity × launched × viral boost."""
        weights = self.pop * (self.launch_day <= d)
        self.viral_today = [p for p, a, b in self.viral if a <= d < b]
        for p in self.viral_today:
            weights[p] *= C.VIRAL_VIEW_MULT
        self.day_w = [weights[idx] for idx in self.sub_products]
        self.launched = np.flatnonzero(self.launch_day <= d)

    def _pick_sub(self, st: UserState, is_gift: bool) -> int:
        if is_gift:
            options = [s for s in self.gift_subs if s not in st.prefs] or self.gift_subs
            return self.r.choice(options)
        if self.r.random() < C.TASTE_PROB:
            return self.r.choices(st.prefs, st.pref_w)[0]
        return int(np.searchsorted(np.cumsum(self.sub_weights), self.r.random(), side="right").clip(
            0, len(self.subs) - 1))

    def _pick_product(self, st: UserState, s: int, d: int) -> int:
        idx = self.sub_products[s]
        w = self.day_w[s] * np.where(np.abs(self.price_q[idx] - st.comfort) <= C.COMFORT_WIDTH,
                                     C.COMFORT_MULT, C.OUTSIDE_COMFORT_MULT)
        if st.minor:
            w = w * ~self.adult[idx]
        for table, mult in ((st.blocks, C.SUBSTITUTE_AFTER_PURCHASE_MULT), (st.boosts,
                                                                           C.COMPLEMENT_AFTER_PURCHASE_MULT)):
            active = table.get(s)
            if active:
                pos = {p: k for k, p in enumerate(idx)} if len(active) > 0 else {}
                for p, until in list(active.items()):
                    if until < d:
                        del active[p]
                    elif p in pos:
                        w[pos[p]] *= mult
        total = w.sum()
        if total <= 0:
            return -1
        return int(idx[min(len(idx) - 1, np.searchsorted(np.cumsum(w), self.r.random() * total))])

    def _ok(self, st: UserState, p: int, d: int) -> bool:
        """Can this user see product p today? (launched already; never age-restricted for minors)"""
        return self.launch_day[p] <= d and not (st.minor and self.adult[p])

    def _random_product(self, st: UserState) -> int:
        """Noise: any launched product, uniformly (skipping age-restricted ones for minors)."""
        for _ in range(5):
            p = int(self.launched[self.r.randrange(len(self.launched))])
            if not (st.minor and self.adult[p]):
                return p
        return -1

    def _new_session(self, u: int, start: int, is_gift: bool = False) -> int:
        self.s_user.append(u)
        self.s_start.append(start)
        device = self.device[u]
        if self.r.random() < C.SECOND_DEVICE_RATE:
            device = "desktop" if device == "mobile" else "mobile"
        self.s_device.append(device)
        self.s_gift.append(int(is_gift))
        self._t = start
        self.state[u].last_session = start // 86400
        return len(self.s_user) - 1

    def _emit(self, u: int, p: int, sid: int, etype: int, source: int, query: str = "") -> None:
        self.e_user.append(u)
        self.e_prod.append(p)
        self.e_sess.append(sid)
        self.e_type.append(etype)
        self.e_time.append(self._t)
        self.e_src.append(source)
        self.e_query.append(query)
        self._t += self.r.randint(10, 120)

    def _session_start(self, d: int) -> int:
        hour = self.r.choices(range(24), C.HOUR_WEIGHTS)[0]
        return d * 86400 + hour * 3600 + self.r.randrange(3600)

    def _multiplier(self, st: UserState, p: int, is_gift: bool) -> float:
        taste = C.TASTE_MULT if (self.sub[p] in st.prefs or is_gift) else 1.0
        comfort = C.COMFORT_MULT if abs(self.price_q[p] - st.comfort) <= C.COMFORT_WIDTH else C.OUTSIDE_COMFORT_MULT
        rating = C.HIGH_RATING_MULT if self.rating[p] >= C.HIGH_RATING else 1.0
        return taste * comfort * rating

    def _search_query(self, p: int) -> str:
        pattern = self.r.choice(SEARCH_PATTERNS)
        budget = int(np.ceil(self.price[p] * 1.2 / 500) * 500) if self.price[p] >= 500 else 500
        return pattern.format(q=self.query[p], brand=self.brand[p].lower(), price=budget).strip()

    # ------------------------------------------------------------------ one product step
    def _interact(self, u: int, st: UserState, p: int, sid: int, source: int, conv: float, browse_only: bool,
                  is_gift: bool, cart: list[tuple[int, float]], day: int) -> int:
        """View a product and maybe go further down the funnel. Returns events written."""
        m = self._multiplier(st, p, is_gift)
        soft = (m / C.TYPICAL_MULT) ** 0.5
        self._emit(u, p, sid, VIEW, source)
        n = 1
        clicked = False
        if self.r.random() < min(C.MAX_PROB, C.CLICK_RATE * soft):
            self._emit(u, p, sid, CLICK, PAGE)
            clicked, n = True, n + 1
        if self.r.random() < min(C.MAX_PROB, C.WISHLIST_RATE * soft):
            self._emit(u, p, sid, WISH, PAGE)
            self.today_wishes.append((u, p))
            n += 1
        in_cart = any(c == p for c, _ in cart)
        if (not browse_only and not in_cart and self.stock_ok[p]
                and self.r.random() < min(C.MAX_PROB, C.ADD_TO_CART_RATE * m * conv)):
            if not clicked:
                self._emit(u, p, sid, CLICK, PAGE)
                n += 1
            self._emit(u, p, sid, CART, PAGE)
            cart.append((p, m))
            n += 1
        return n

    def _checkout(self, u: int, st: UserState, sid: int, cart: list[tuple[int, float]], conv: float,
                  day: int) -> None:
        """Decide what happens to each cart item: removed, bought, or left (abandoned cart)."""
        for p, m in cart:
            if self.r.random() < C.REMOVE_FROM_CART_RATE:
                self._emit(u, p, sid, REMOVE, PAGE)
            elif self.r.random() < min(C.MAX_PROB, C.PURCHASE_RATE * (m / C.TYPICAL_MULT) ** 0.5 * conv):
                self._buy(u, st, p, sid, day, PAGE, None)
            else:
                self.today_carts.append((u, p))
        self._fill_basket(u, st, sid, day)

    def _fill_basket(self, u: int, st: UserState, sid: int, day: int) -> None:
        """Orders often have more than one item: add 'frequently bought together' extras."""
        bought = [p for s, _, p, *_ in self.purchases[-5:] if s == sid]
        if not bought:
            return
        sizes = list(C.ITEMS_PER_ORDER)
        wanted = self.r.choices(sizes, [C.ITEMS_PER_ORDER[k] for k in sizes])[0]
        for _ in range(wanted - len(bought)):
            comps = [q for p in bought for q in self.comps.get(p, [])
                     if q not in bought and self.stock_ok[q] and self._ok(st, q, day)]
            p = self.r.choice(comps) if comps else self._pick_product(st, self._pick_sub(st, False), day)
            if p < 0 or p in bought or not self.stock_ok[p] or not self._ok(st, p, day):
                continue
            self._emit(u, p, sid, VIEW, RECO)
            self._emit(u, p, sid, CART, PAGE)
            self._buy(u, st, p, sid, day, PAGE, None)
            bought.append(p)

    def _buy(self, u: int, st: UserState, p: int, sid: int, day: int, source: int, offer: dict | None) -> None:
        qty = self.r.randint(*C.CONSUMABLE_QTY) if self.consumable[p] else 1
        self.purchases.append((sid, u, p, self._t, qty, self.offer_sim.price_paid(offer, p)))
        if st.last_order_sid != sid:
            st.last_order_sid = sid
            self.order_count[u] += 1
        self._emit(u, p, sid, BUY, source)
        self._after_purchase(u, st, p, day)

    def _after_purchase(self, u: int, st: UserState, p: int, day: int) -> None:
        """Plant the post-purchase patterns."""
        st.last_bought[p] = day
        if self.consumable[p]:
            gap = self.rep_days[p] * self.r.uniform(1 - C.REPURCHASE_JITTER, 1 + C.REPURCHASE_JITTER)
            if self.r.random() < C.REPURCHASE_PROB:
                self.scheduled.setdefault(day + max(1, round(gap)), []).append(("repurchase", u, p))
            else:  # didn't come back by itself -> the offer pipeline sees "repurchase due"
                self.due_triggers.setdefault(day + int(self.rep_days[p]) + 2, []).append((u, p))
            return
        until = day + C.SUBSTITUTE_AFTER_PURCHASE_DAYS
        for q in [p] + self.subst.get(p, []):
            st.blocks.setdefault(int(self.sub[q]), {})[q] = until
        comps = self.comps.get(p, [])
        if comps:
            until = day + C.COMPLEMENT_AFTER_PURCHASE_DAYS
            for q in comps:
                st.boosts.setdefault(int(self.sub[q]), {})[q] = until
                st.boost_list.append((q, until))
            self.today_bought.append((u, p))
            if self.r.random() < C.COMPLEMENT_BUY_PROB:
                when = day + self.r.randint(1, C.COMPLEMENT_AFTER_PURCHASE_DAYS)
                self.scheduled.setdefault(when, []).append(("complement", u, self.r.choice(comps)))

    # ------------------------------------------------------------------ session types
    def normal_session(self, u: int, d: int, conv: float) -> None:
        st = self.state[u]
        is_gift = st.gifter and self.r.random() < C.GIFT_SESSION_RATE
        browse_only = self.r.random() < C.BROWSE_ONLY_SESSION_RATE
        sid = self._new_session(u, self._session_start(d), is_gift)
        target = self.r.randint(*C.EVENTS_PER_SESSION)
        cart: list[tuple[int, float]] = []
        n = 0

        if self.r.random() < C.SEARCH_SESSION_RATE:
            s = self._pick_sub(st, is_gift)
            first = self._pick_product(st, s, d)
            if first >= 0:
                self._emit(u, -1, sid, SEARCH, SRC_SEARCH, self._search_query(first))
                n += 1
                results = [first] + [self._pick_product(st, s, d) for _ in range(self.r.randint(1, 3))]
                for p in dict.fromkeys(results):     # unique, keeps order
                    if p >= 0:
                        n += self._interact(u, st, p, sid, SRC_SEARCH, conv, browse_only, is_gift, cart, d)

        boosts = [q for q, until in st.boost_list if until >= d and self._ok(st, q, d)]
        st.boost_list = [(q, until) for q, until in st.boost_list if until >= d]
        if boosts and self.r.random() < C.COMPLEMENT_NUDGE_PROB:   # "frequently bought together"
            n += self._interact(u, st, self.r.choice(boosts), sid, RECO, conv, browse_only, is_gift, cart, d)

        steps = 0
        while n < target and steps < 30:
            steps += 1
            r = self.r.random()
            if r < C.NOISE_CLICK_RATE:
                p = self._random_product(st)
                if p >= 0:
                    self._emit(u, p, sid, VIEW, HOME)
                    self._emit(u, p, sid, CLICK, PAGE)
                    n += 2
                continue
            if self.viral_today and r < C.NOISE_CLICK_RATE + C.VIRAL_PICK_RATE and not st.minor:
                p, source = self.r.choice(self.viral_today), HOME
            else:
                p = self._pick_product(st, self._pick_sub(st, is_gift), d)
                source = HOME if n == 0 else self.r.choice((PAGE, PAGE, RECO, HOME))
            if p >= 0:
                n += self._interact(u, st, p, sid, source, conv, browse_only, is_gift, cart, d)
        self._checkout(u, st, sid, cart, conv, d)

    def repurchase_session(self, u: int, p: int, d: int) -> None:
        sid = self._new_session(u, self._session_start(d))
        source = HOME
        if self.r.random() < 0.3:
            self._emit(u, -1, sid, SEARCH, SRC_SEARCH, self.query[p])
            source = SRC_SEARCH
        self._emit(u, p, sid, VIEW, source)
        self._emit(u, p, sid, CART, PAGE)
        self._buy(u, self.state[u], p, sid, d, PAGE, None)

    def complement_session(self, u: int, p: int, d: int) -> None:
        st = self.state[u]
        if not self._ok(st, p, d):
            return
        sid = self._new_session(u, self._session_start(d))
        self._emit(u, p, sid, VIEW, RECO)
        self._emit(u, p, sid, CLICK, PAGE)
        self._emit(u, p, sid, CART, PAGE)
        self._buy(u, st, p, sid, d, PAGE, None)

    def offer_session(self, action: dict, d: int) -> None:
        """A user tapped an offer in Telegram (or, for the holdout, came back on their own)."""
        u, p, offer = action["user"], action["product"], action["offer"]
        st = self.state[u]
        if action["time"] >= WINDOW_SECONDS or not self._ok(st, p, d):
            return
        telegram = action["kind"] == "offer"
        sid = self._new_session(u, action["time"])
        self._emit(u, p, sid, VIEW, TELEGRAM if telegram else HOME)
        self._emit(u, p, sid, CLICK, PAGE)
        if action["redeem"] or not telegram:
            self._emit(u, p, sid, CART, PAGE)
            self._buy(u, st, p, sid, d, TELEGRAM if telegram else PAGE, offer if telegram else None)

    # ------------------------------------------------------------------ offers
    def _prefers(self, u: int, p: int) -> bool:
        return p >= 0 and int(self.sub[p]) in self.state[u].prefs

    def _offer_triggers(self, d: int) -> list[tuple[int, str, int, int]]:
        """Yesterday's behaviour -> today's triggers: (user, trigger_type, anchor, target)."""
        trig: list[tuple[int, str, int, int]] = []
        trig += [(u, "cart_abandon", p, p) for u, p in self.today_carts]
        trig += [(u, "price_drop", p, p) for u, p in self.today_wishes if self.r.random() < C.PRICE_DROP_PROB]
        for u, p in self.today_bought:
            options = [q for q in self.comps[p] if self._ok(self.state[u], q, d)]
            if options:
                trig.append((u, "post_purchase", p, self.r.choice(options)))
        for u, p in self.due_triggers.pop(d, []):
            if self.state[u].last_bought.get(p, -1) <= d - self.rep_days[p]:
                trig.append((u, "repurchase_due", p, p))
        date = day_to_date(d)
        if date.weekday() == 0:   # Mondays: win back users who went quiet
            quiet = np.flatnonzero(self.offer_sim.opted == 1)
            for u in quiet:
                st = self.state[u]
                if self.order_count[u] and d - st.last_session >= C.WIN_BACK_DAYS and d - st.last_winback >= 30:
                    st.last_winback = d
                    target = self._pick_product(st, st.prefs[0], d)
                    if target >= 0:
                        trig.append((int(u), "win_back", -1, target))
        if any(day_index(start) == d for start, _, _ in C.SALE_WINDOWS):
            for u in np.flatnonzero(self.offer_sim.opted == 1):
                if self.r.random() < C.FESTIVAL_REACH and self.signup_day[u] <= d:
                    st = self.state[u]
                    target = self._pick_product(st, self._pick_sub(st, False), d)
                    if target >= 0:
                        trig.append((int(u), "festival", -1, target))
        return trig

    # ------------------------------------------------------------------ main loop
    def run(self) -> SimResult:
        sale_days = set()
        for start, end, _ in C.SALE_WINDOWS:
            sale_days.update(range(day_index(start), day_index(end) + 1))

        for d in range(N_DAYS):
            self._prepare_day(d)
            sale = d in sale_days
            conv = C.SALE_CONVERSION_MULT if sale else 1.0
            traffic = (C.SALE_TRAFFIC_MULT if sale else 1.0) * (
                C.WEEKEND_TRAFFIC_MULT if day_to_date(d).weekday() >= 5 else 1.0)

            # 1. Offers decided from yesterday's behaviour.
            triggers = self._offer_triggers(d)
            self.today_carts, self.today_wishes, self.today_bought = [], [], []
            for action in self.offer_sim.process(d, triggers, self.order_count, self._prefers):
                self.offer_session(action, d)

            # 2. Planned sessions (repurchases, complements).
            for kind, u, p in self.scheduled.pop(d, []):
                if kind == "repurchase":
                    self.repurchase_session(u, p, d)
                else:
                    self.complement_session(u, p, d)

            # 3. Ordinary browsing sessions (Poisson number per user).
            lam = self.rate * traffic * (self.signup_day <= d)
            counts = self.rng.poisson(lam)
            for u in np.flatnonzero(counts):
                for _ in range(int(counts[u])):
                    self.normal_session(int(u), d, conv)
        return self._finish()

    # ------------------------------------------------------------------ build tables
    def _finish(self) -> SimResult:
        uid = np.array(self.user_ids)
        pid = np.array(self.products["product_id"].tolist() + [""])   # index -1 -> ""

        # Sessions: number S0000001... in order of start time.
        s_start = np.array(self.s_start, dtype=np.int64)
        s_order = np.argsort(s_start, kind="stable")
        s_new = np.empty(len(s_order), dtype=np.int64)
        s_new[s_order] = np.arange(len(s_order))
        sessions = pd.DataFrame({
            "session_id": [f"S{i + 1:07d}" for i in range(len(s_order))],
            "user_id": uid[np.array(self.s_user, dtype=int)[s_order]] if len(s_order) else [],
            "start_time": seconds_to_iso(s_start[s_order]),
            "device": np.array(self.s_device)[s_order] if len(s_order) else [],
            "is_gift": np.array(self.s_gift, dtype=int)[s_order] if len(s_order) else [],
        })
        sid_text = sessions["session_id"].to_numpy()

        # Events: keep only those inside the window; number E00000001... in time order.
        e_time = np.array(self.e_time, dtype=np.int64)
        keep = np.flatnonzero(e_time < WINDOW_SECONDS)
        order = keep[np.argsort(e_time[keep], kind="stable")]
        e_prod = np.array(self.e_prod, dtype=int)[order]
        events = pd.DataFrame({
            "event_id": [f"E{i + 1:08d}" for i in range(len(order))],
            "user_id": uid[np.array(self.e_user, dtype=int)[order]],
            "product_id": pid[e_prod],
            "session_id": sid_text[s_new[np.array(self.e_sess, dtype=int)[order]]],
            "event_type": np.array(EVENT_TYPES)[np.array(self.e_type, dtype=int)[order]],
            "timestamp": seconds_to_iso(e_time[order]),
            "source": np.array(SOURCES)[np.array(self.e_src, dtype=int)[order]],
            "search_query": np.array(self.e_query, dtype=object)[order],
        })

        bought = pd.DataFrame(self.purchases, columns=["_sess", "_user", "_prod", "_time", "quantity",
                                                       "price_paid"])
        bought = bought[bought["_time"] < WINDOW_SECONDS].reset_index(drop=True)
        purchases = pd.DataFrame({
            "session_id": sid_text[s_new[bought["_sess"].to_numpy(dtype=int)]],
            "user_id": uid[bought["_user"].to_numpy(dtype=int)],
            "product_id": pid[bought["_prod"].to_numpy(dtype=int)],
            "timestamp": seconds_to_iso(bought["_time"].to_numpy()),
            "quantity": bought["quantity"].astype(int),
            "price_paid": bought["price_paid"].astype(float),
        })
        return SimResult(sessions, events, purchases, self.offer_sim.sends_table(WINDOW_SECONDS))
