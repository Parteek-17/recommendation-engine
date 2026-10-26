"""Create the tiny, realistic fixture CSVs in data/sample/ (seeded -> identical every run).

Run:  python scripts/make_sample_data.py

Size: 50 users, 100 products, 8 parent + 16 sub categories, ~2,000 events, 20 offers.

Planted cases (so every phase can test its special logic on day 1):
  P00001 Samsung phone  --complement-->  P00002 Spigen cover      (U00001 buys phone, then cover)
  P00001 Samsung phone  --substitute-->  P00003 Redmi phone       (similar price, same sub-category)
  P00004 Aashirvaad Atta: consumable, repurchase_days=30          (U00002 buys it every ~30 days)
  P00005 Chef Knife Set + "Party Card Game (18+)": age_restricted (U00007 is a minor)
  U00003: a gift session (is_gift=1) buying toys
  U00004: cart abandon on 2026-09-28 (add_to_cart, no purchase)
  U00050: brand-new user, signed up 2026-09-28, no events (cold start)
  Offers: OF0017 = 50% off (breaks MAX_DISCOUNT_PCT), OF0018 = expired, OF0019 = inactive,
          OF0013 = needs approval  -> handy for guardrail tests.

This is NOT the full generator (that is Phase 1, data_gen/). It is just enough data to build against.
"""

from __future__ import annotations

import csv
import math
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Let "python scripts/make_sample_data.py" find the common/ package in the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.db import columns  # noqa: E402  (import after sys.path fix)

SEED = 42
OUT = Path(__file__).resolve().parent.parent / "data" / "sample"
WINDOW_START = datetime(2026, 4, 1)
WINDOW_END = datetime(2026, 9, 30, 23, 59)

rng = random.Random(SEED)

# ---------------------------------------------------------------------------
# Catalog definition
# ---------------------------------------------------------------------------
PARENTS = {
    "C001": "Electronics", "C002": "Fashion", "C003": "Home & Kitchen", "C004": "Beauty",
    "C005": "Grocery", "C006": "Sports", "C007": "Books", "C008": "Toys",
}
# Parent margins (%) from docs/DATA_PARAMETERS.md
MARGINS = {
    "C001": (5, 15), "C002": (30, 50), "C003": (20, 35), "C004": (25, 40),
    "C005": (5, 12), "C006": (20, 35), "C007": (10, 25), "C008": (25, 40),
}
# sub_id: (name, parent, product types, brands, (price_lo, price_hi), repurchase_days range or None)
SUBS = {
    "C009": ("Mobiles", "C001", ["5G Smartphone (6GB/128GB)", "5G Smartphone (8GB/256GB)", "Smartphone (4GB/64GB)"],
             ["Samsung", "Redmi", "Realme", "OnePlus", "iQOO", "Motorola"], (7999, 34999), None),
    "C010": ("Mobile Accessories", "C001", ["Back Cover", "20W Fast Charger", "Wireless Earbuds", "Tempered Glass",
             "Power Bank 10000mAh"], ["boAt", "Spigen", "Ambrane", "Portronics", "Noise"], (199, 2999), None),
    "C011": ("Men's Clothing", "C002", ["Cotton Slim Fit T-shirt", "Denim Jeans", "Casual Shirt", "Cotton Kurta"],
             ["Roadster", "Allen Solly", "Levi's", "Peter England", "Manyavar"], (299, 2999), None),
    "C012": ("Footwear", "C002", ["Running Shoes", "Casual Sneakers", "Flip Flops", "Formal Shoes"],
             ["Puma", "Campus", "Bata", "Red Tape", "Sparx"], (249, 4999), None),
    "C013": ("Kitchen Appliances", "C003", ["Mixer Grinder 750W", "Electric Kettle 1.5L", "Pressure Cooker 5L",
             "Induction Cooktop"], ["Prestige", "Bajaj", "Pigeon", "Philips", "Butterfly"], (499, 6999), None),
    "C014": ("Home Decor", "C003", ["Cotton Double Bedsheet", "Wall Clock", "Cushion Covers (Set of 5)",
             "LED String Lights"], ["Bombay Dyeing", "Ajanta", "Home Centre", "Story@Home"], (249, 1999), None),
    "C015": ("Skincare", "C004", ["Vitamin C Face Serum 30ml", "Sunscreen SPF 50 50g", "Face Wash 100ml",
             "Moisturiser 200ml"], ["Minimalist", "Mamaearth", "Lakme", "Nivea", "Himalaya", "Plum"], (149, 899), (30, 60)),
    "C016": ("Haircare", "C004", ["Anti-Dandruff Shampoo 340ml", "Hair Oil 300ml", "Conditioner 180ml"],
             ["Dove", "Parachute", "Indulekha", "Head & Shoulders", "Bajaj Almond"], (120, 699), (30, 45)),
    "C017": ("Staples", "C005", ["Whole Wheat Atta 5kg", "Basmati Rice 5kg", "Toor Dal 1kg",
             "Sunflower Oil 1L"], ["Aashirvaad", "India Gate", "Tata Sampann", "Fortune", "Daawat"], (99, 899), (15, 45)),
    "C018": ("Beverages", "C005", ["Green Tea (100 bags)", "Instant Coffee 100g", "Premium Tea 500g"],
             ["Tata Tea", "Nescafe", "Bru", "Lipton", "Red Label"], (120, 550), (15, 30)),
    "C019": ("Fitness Equipment", "C006", ["Yoga Mat 6mm", "Adjustable Dumbbells 10kg", "Skipping Rope",
             "Resistance Bands Set"], ["Boldfit", "Domyos", "Strauss", "Cockatoo"], (199, 3999), None),
    "C020": ("Sportswear", "C006", ["Dry-Fit Training T-shirt", "Track Pants", "Sports Socks (Pack of 3)"],
             ["Kalenji", "Puma", "Adidas", "HRX"], (199, 1999), None),
    "C021": ("Fiction & Self-help", "C007", ["Atomic Habits", "The Psychology of Money", "Ikigai", "The White Tiger",
             "Wings of Fire", "The God of Small Things"], ["Penguin", "HarperCollins", "Rupa", "Jaico"], (199, 599), None),
    "C022": ("Exam Prep", "C007", ["NCERT Physics Class 12", "Quantitative Aptitude", "Indian Polity",
             "General Knowledge 2026"], ["Arihant", "McGraw Hill", "Disha", "S. Chand"], (149, 899), None),
    "C023": ("Kids Toys", "C008", ["Remote Control Car", "Building Blocks (500 pcs)", "Soft Teddy Bear",
             "Doctor Play Set"], ["Funskool", "Hot Wheels", "Lego", "Mirada"], (299, 3499), None),
    "C024": ("Board Games", "C008", ["Monopoly Classic", "Wooden Chess Set", "Scrabble", "Party Card Game (18+)"],
             ["Funskool", "Hasbro", "Mattel"], (299, 1499), None),
}
BOOK_SUBS = ("C021", "C022")
# Complement rules between sub-categories (ground truth): buying left makes right more useful.
COMPLEMENT_SUBS = {"C009": ["C010"], "C019": ["C020"], "C012": ["C020"], "C013": ["C017"]}

CITIES = {
    1: ["Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Chennai", "Pune", "Kolkata"],
    2: ["Jaipur", "Lucknow", "Indore", "Chandigarh", "Ludhiana", "Coimbatore", "Nagpur"],
    3: ["Bathinda", "Moga", "Hoshiarpur", "Jhansi", "Bareilly", "Hubli"],
}


def nice_price(x: float) -> int:
    """Round to an Indian-store looking price ending in 9 (e.g. 1287 -> 1289)."""
    x = int(round(x))
    return x - x % 10 + 9


def log_uniform(lo: float, hi: float) -> float:
    return math.exp(rng.uniform(math.log(lo), math.log(hi)))


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def build_categories() -> list[dict]:
    rows = [{"category_id": cid, "name": name, "parent_id": ""} for cid, name in PARENTS.items()]
    rows += [{"category_id": sid, "name": s[0], "parent_id": s[1]} for sid, s in SUBS.items()]
    return rows


def make_product(pid: str, sub_id: str, ptype: str, brand: str, price: int | None = None,
                 launch: datetime | None = None) -> dict:
    name, parent, _, _, (lo, hi), rep = SUBS[sub_id]
    price = price or nice_price(log_uniform(lo, hi))
    mrp = max(price, nice_price(price * (1 + rng.uniform(0, 0.4))))
    title = f"{ptype} (Paperback) by {brand}" if sub_id in BOOK_SUBS else f"{brand} {ptype}"
    if launch is None:
        # 10% are recent launches (cold-start items), the rest launched before/early in the window.
        if rng.random() < 0.10:
            launch = datetime(2026, 8, 20) + timedelta(days=rng.randint(0, 36))
        else:
            launch = datetime(2025, 1, 1) + timedelta(days=rng.randint(0, 470))
    age_restricted = int("Knife" in ptype or "18+" in ptype)
    return {
        "product_id": pid,
        "title": title,
        "description": f"{title}. Popular choice in {name}. Genuine product with brand warranty and easy returns.",
        "category_id": sub_id,
        "brand": brand,
        "price": price,
        "mrp": mrp,
        "margin_pct": round(rng.uniform(*MARGINS[parent]), 1),
        "avg_rating": round(min(5.0, max(1.0, rng.gauss(4.0, 0.4))), 1),
        "rating_count": int(min(20000, rng.paretovariate(1.2) * 20)),
        "stock_qty": 0 if rng.random() < 0.05 else rng.randint(5, 500),
        "launch_date": launch.date().isoformat(),
        "tags": "|".join([name.lower(), brand.lower()] + [w.lower() for w in ptype.split() if w.isalpha()][:3]),
        "is_consumable": int(rep is not None),
        "repurchase_days": rng.randint(*rep) if rep else "",
        "age_restricted": age_restricted,
    }


def build_products() -> list[dict]:
    early = datetime(2025, 6, 1)
    products = [
        make_product("P00001", "C009", "Galaxy M35 5G (6GB/128GB)", "Samsung", 15999, early),
        make_product("P00002", "C010", "Rugged Armor Back Cover for Galaxy M35", "Spigen", 799, early),
        make_product("P00003", "C009", "Note 13 5G (8GB/128GB)", "Redmi", 16999, early),
        make_product("P00004", "C017", "Whole Wheat Atta 5kg", "Aashirvaad", 289, early),
        make_product("P00005", "C013", "Chef Knife Set (5 pcs)", "Prestige", 1499, early),
    ]
    products[3]["repurchase_days"] = 30  # planted repurchase cycle
    products[0]["stock_qty"] = products[1]["stock_qty"] = products[2]["stock_qty"] = 120

    seen = {p["title"] for p in products}
    sub_ids = list(SUBS)
    i = 0
    while len(products) < 100:
        sub_id = sub_ids[i % len(sub_ids)]
        i += 1
        _, _, types, brands, _, _ = SUBS[sub_id]
        ptype, brand = rng.choice(types), rng.choice(brands)
        p = make_product(f"P{len(products) + 1:05d}", sub_id, ptype, brand)
        if p["title"] in seen:
            continue
        seen.add(p["title"])
        products.append(p)
    return products


def build_users() -> tuple[list[dict], list[dict]]:
    """Return (users, hidden_profiles). Hidden profiles are ground truth only."""
    users, hidden = [], []
    sub_ids = list(SUBS)
    for n in range(1, 51):
        uid = f"U{n:05d}"
        age = rng.choices(["13-17", "18-24", "25-34", "35-44", "45+"], [5, 30, 35, 18, 12])[0]
        if uid == "U00007":
            age = "13-17"  # planted minor
        elif age == "13-17":
            age = "18-24"  # keep exactly one minor in the small sample
        tier = rng.choices([1, 2, 3], [40, 35, 25])[0]
        signup = datetime(2025, 6, 1) + timedelta(days=rng.randint(0, 380))
        if uid == "U00050":
            signup = datetime(2026, 9, 28)  # planted brand-new user
        users.append({
            "user_id": uid, "signup_date": signup.date().isoformat(), "age_group": age,
            "gender": rng.choices(["F", "M", "Other"], [45, 53, 2])[0],
            "city": rng.choice(CITIES[tier]), "city_tier": tier,
            "device": rng.choices(["mobile", "desktop"], [75, 25])[0], "is_minor": int(age == "13-17"),
        })
        prefs = rng.sample(sub_ids, rng.randint(2, 3))
        if uid == "U00001":
            prefs = ["C009", "C010", "C019"]
        if uid == "U00002":
            prefs = ["C017", "C018", "C015"]
        hidden.append({
            "user_id": uid,
            "preferred_categories": "|".join(prefs),
            "price_sensitivity": round(min(1.0, rng.betavariate(2, 2) + (0.1 if tier == 3 else 0)), 2),
            "activity_level": rng.choices(["light", "regular", "heavy"], [50, 35, 15])[0],
            "is_gifter": int(uid == "U00003" or rng.random() < 0.15),
        })
    return users, hidden


# ---------------------------------------------------------------------------
# Behaviour simulation
# ---------------------------------------------------------------------------


def random_session_start(signup: str) -> datetime:
    start = max(WINDOW_START, datetime.fromisoformat(signup))
    day = start + timedelta(days=rng.randint(0, max(0, (WINDOW_END - start).days)))
    # Evening peak (7-11 PM), quiet at night.
    hour = rng.choices(range(24), [1, 1, 1, 1, 1, 1, 1, 2, 3, 4, 4, 4, 5, 5, 4, 4, 4, 5, 6, 9, 10, 10, 8, 4])[0]
    return day.replace(hour=hour, minute=rng.randint(0, 59), second=rng.randint(0, 59))


class Sim:
    """Collects sessions/events/purchases; IDs are assigned at the end, sorted by time."""

    def __init__(self, products: list[dict]):
        self.products = products
        self.by_sub: dict[str, list[dict]] = {}
        for p in products:
            self.by_sub.setdefault(p["category_id"], []).append(p)
        # Long-tail popularity: Zipf weights over a shuffled product order.
        order = products[:]
        rng.shuffle(order)
        self.pop = {p["product_id"]: 1 / (rank + 1) ** 1.1 for rank, p in enumerate(order)}
        self.sessions: list[dict] = []  # each: {user_id, start_time, device, is_gift, events: [...]}

    def pick_product(self, sub_id: str, sensitivity: float, is_minor: bool) -> dict | None:
        options = [p for p in self.by_sub[sub_id] if not (is_minor and p["age_restricted"])]
        if not options:
            return None
        # Price-sensitive users favour cheaper items: weight ~ popularity / price^sensitivity.
        weights = [self.pop[p["product_id"]] / (p["price"] ** sensitivity) for p in options]
        return rng.choices(options, weights)[0]

    def add_session(self, user: dict, start: datetime, steps: list[tuple[str, dict | None, str]],
                    is_gift: bool = False) -> None:
        """steps: list of (event_type, product or None, search_query)."""
        events, t = [], start
        for event_type, product, query in steps:
            events.append({"event_type": event_type, "product_id": product["product_id"] if product else "",
                           "timestamp": t, "search_query": query, "price": product["price"] if product else 0})
            t += timedelta(seconds=rng.randint(20, 180))
        self.sessions.append({"user_id": user["user_id"], "start_time": start, "device": user["device"],
                              "is_gift": int(is_gift), "events": events})

    def random_session(self, user: dict, profile: dict) -> None:
        prefs = profile["preferred_categories"].split("|")
        sens = profile["price_sensitivity"]
        minor = bool(user["is_minor"])
        is_gift = bool(profile["is_gifter"]) and rng.random() < 0.10
        all_subs = list(SUBS)
        steps: list[tuple[str, dict | None, str]] = []
        if rng.random() < 0.10:
            sub = rng.choice(prefs)
            steps.append(("search", None, SUBS[sub][0].lower()))
        for _ in range(rng.randint(2, 7)):
            if is_gift:
                sub = rng.choice([s for s in ("C023", "C024", "C021", "C014") if s not in prefs] or ["C023"])
            elif rng.random() < 0.8:
                sub = rng.choice(prefs)
            else:
                sub = rng.choice(all_subs)
            p = self.pick_product(sub, sens, minor)
            if p is None:
                continue
            steps.append(("view", p, ""))
            if rng.random() < 0.30:
                steps.append(("click", p, ""))
            if rng.random() < 0.05:
                steps.append(("wishlist", p, ""))
            boost = 2.0 if sub in prefs else 1.0
            if rng.random() < 0.10 * boost and p["stock_qty"] > 0:
                steps.append(("add_to_cart", p, ""))
                r = rng.random()
                if r < 0.15:
                    steps.append(("remove_from_cart", p, ""))
                elif r < 0.15 + 0.35 * boost:
                    steps.append(("purchase", p, ""))
        if steps:
            self.add_session(user, random_session_start(user["signup_date"]), steps, is_gift)


def simulate(users: list[dict], hidden: list[dict], products: list[dict]) -> Sim:
    sim = Sim(products)
    pid = {p["product_id"]: p for p in products}
    profiles = {h["user_id"]: h for h in hidden}
    counts = {"light": (2, 3), "regular": (4, 8), "heavy": (10, 15)}  # sessions per user (tuned to ~2k events)
    for user in users:
        if user["user_id"] == "U00050":
            continue  # planted cold-start user: no history at all
        prof = profiles[user["user_id"]]
        for _ in range(rng.randint(*counts[prof["activity_level"]])):
            sim.random_session(user, prof)

    u = {x["user_id"]: x for x in users}
    # Planted: U00001 compares phones (substitutes), buys P00001, then buys the cover 2 days later.
    sim.add_session(u["U00001"], datetime(2026, 6, 10, 20, 5), [
        ("search", None, "5g phone under 20000"), ("view", pid["P00001"], ""), ("view", pid["P00003"], ""),
        ("click", pid["P00001"], ""), ("add_to_cart", pid["P00001"], ""), ("purchase", pid["P00001"], "")])
    sim.add_session(u["U00001"], datetime(2026, 6, 12, 21, 15), [
        ("view", pid["P00002"], ""), ("add_to_cart", pid["P00002"], ""), ("purchase", pid["P00002"], "")])
    # Planted: U00002 re-buys atta every ~30 days.
    for day in (datetime(2026, 7, 1, 9, 30), datetime(2026, 7, 31, 10, 0), datetime(2026, 8, 30, 9, 45)):
        sim.add_session(u["U00002"], day, [("view", pid["P00004"], ""), ("add_to_cart", pid["P00004"], ""),
                                           ("purchase", pid["P00004"], "")])
    # Planted: U00003 gift session (toys, outside usual taste) during the Aug sale.
    toys = sim.by_sub["C023"]
    sim.add_session(u["U00003"], datetime(2026, 8, 12, 19, 40), [
        ("search", None, "gift for 6 year old"), ("view", toys[0], ""), ("view", toys[1], ""),
        ("add_to_cart", toys[0], ""), ("purchase", toys[0], "")], is_gift=True)
    # Planted: U00004 abandons a cart 2 days before the window ends.
    shoes = sim.by_sub["C012"][0]
    sim.add_session(u["U00004"], datetime(2026, 9, 28, 22, 10), [
        ("view", shoes, ""), ("click", shoes, ""), ("add_to_cart", shoes, "")])
    return sim


# ---------------------------------------------------------------------------
# Turn the simulation into contract tables
# ---------------------------------------------------------------------------


def finalize(sim: Sim, products: list[dict]) -> dict[str, list[dict]]:
    pid = {p["product_id"]: p for p in products}
    sources = ["home", "product_page", "recommendation"]
    sessions, events, orders, items, reviews = [], [], [], [], []
    flat, pending_orders = [], []
    for n, s in enumerate(sorted(sim.sessions, key=lambda s: s["start_time"]), start=1):
        sid = f"S{n:07d}"
        sessions.append({"session_id": sid, "user_id": s["user_id"], "start_time": iso(s["start_time"]),
                         "device": s["device"], "is_gift": s["is_gift"]})
        has_search = any(e["event_type"] == "search" for e in s["events"])
        base_source = "search" if has_search else rng.choice(sources)
        for e in s["events"]:
            flat.append({**e, "session_id": sid, "user_id": s["user_id"],
                         "source": "search" if e["event_type"] == "search" else base_source})
        bought = [e for e in s["events"] if e["event_type"] == "purchase"]
        if bought:  # one order per session that had purchases
            pending_orders.append({"user_id": s["user_id"], "order_date": bought[-1]["timestamp"], "bought": bought})

    for n, e in enumerate(sorted(flat, key=lambda e: (e["timestamp"], e["session_id"])), start=1):
        events.append({"event_id": f"E{n:08d}", "user_id": e["user_id"], "product_id": e["product_id"],
                       "session_id": e["session_id"], "event_type": e["event_type"],
                       "timestamp": iso(e["timestamp"]), "source": e["source"], "search_query": e["search_query"]})

    review_texts = {
        5: ["Excellent product, totally worth the money!", "Loved it. Fast delivery too."],
        4: ["Good quality for the price.", "Nice product, packaging could be better."],
        3: ["Okay okay. Does the job.", "Average, expected a bit more."],
        2: ["Not happy with the quality.", "Stopped working properly after a few weeks."],
        1: ["Very poor. Returned it.", "Waste of money."],
    }
    for n, o in enumerate(sorted(pending_orders, key=lambda o: o["order_date"]), start=1):
        oid = f"O{n:07d}"
        total = 0
        for e in o["bought"]:
            p = pid[e["product_id"]]
            qty = rng.randint(1, 2) if p["is_consumable"] else 1
            items.append({"order_id": oid, "product_id": p["product_id"], "quantity": qty, "price_paid": p["price"]})
            total += qty * p["price"]
            if rng.random() < 0.15 or p["product_id"] == "P00001":
                rating = int(min(5, max(1, round(p["avg_rating"] + rng.gauss(0, 0.8)))))
                rdate = min(WINDOW_END, e["timestamp"] + timedelta(days=rng.randint(3, 14)))
                reviews.append({"review_id": f"R{len(reviews) + 1:06d}", "user_id": o["user_id"],
                                "product_id": p["product_id"], "rating": rating,
                                "review_text": rng.choice(review_texts[rating]), "review_date": iso(rdate)})
        orders.append({"order_id": oid, "user_id": o["user_id"], "order_date": iso(o["order_date"]),
                       "total_amount": total})
    return {"sessions": sessions, "events": events, "orders": orders, "order_items": items, "reviews": reviews}


def build_relations(products: list[dict]) -> list[dict]:
    """Ground truth complements (by sub-category rules) and substitutes (same sub, price ±30%)."""
    rows = [{"product_id": "P00001", "related_product_id": "P00002", "relation": "complement"},
            {"product_id": "P00001", "related_product_id": "P00003", "relation": "substitute"},
            {"product_id": "P00003", "related_product_id": "P00001", "relation": "substitute"}]
    seen = {(r["product_id"], r["related_product_id"]) for r in rows}
    by_sub: dict[str, list[dict]] = {}
    for p in products:
        by_sub.setdefault(p["category_id"], []).append(p)
    for p in products:
        for target_sub in COMPLEMENT_SUBS.get(p["category_id"], []):
            for q in by_sub.get(target_sub, [])[:3]:
                if (p["product_id"], q["product_id"]) not in seen:
                    seen.add((p["product_id"], q["product_id"]))
                    rows.append({"product_id": p["product_id"], "related_product_id": q["product_id"],
                                 "relation": "complement"})
        for q in by_sub[p["category_id"]]:
            if q is not p and abs(q["price"] - p["price"]) <= 0.3 * p["price"] \
                    and (p["product_id"], q["product_id"]) not in seen:
                seen.add((p["product_id"], q["product_id"]))
                rows.append({"product_id": p["product_id"], "related_product_id": q["product_id"],
                             "relation": "substitute"})
    return rows


def build_offers(products: list[dict]) -> list[dict]:
    yoga = next(p for p in products if p["category_id"] == "C019")  # any fitness item (bundle base)
    wear = next(p for p in products if p["category_id"] == "C020")
    live = ("2026-09-01T00:00:00", "2027-03-31T23:59:59")
    # (code, type, value, min_cart, category, product, bundle_product, segment, trigger, start, end, active, approval)
    spec = [
        ("CART10", "pct_off", 10, 499, "", "", "", "all", "cart_abandon", *live, 1, 0),
        ("ACCESS15", "pct_off", 15, 0, "C010", "", "", "all", "post_purchase", *live, 1, 0),
        ("COMEBACK100", "flat_off", 100, 999, "", "", "", "all", "win_back", *live, 1, 0),
        ("FREESHIP", "free_delivery", 0, 0, "", "", "", "tier3", "cart_abandon", *live, 1, 0),
        ("PHONECOVER", "bundle", 10, 0, "", "P00001", "P00002", "all", "post_purchase", *live, 1, 0),
        ("ATTAAGAIN", "buy_again", 5, 0, "", "P00004", "", "all", "repurchase_due", *live, 1, 0),
        ("DIWALI200", "points", 200, 0, "", "", "", "all", "festival", *live, 1, 0),
        ("TOYS20", "pct_off", 20, 0, "C023", "", "", "all", "festival", *live, 1, 0),
        ("SHOES25", "pct_off", 25, 0, "C012", "", "", "all", "price_drop", *live, 1, 0),
        ("STAPLES50", "flat_off", 50, 499, "C017", "", "", "all", "repurchase_due", *live, 1, 0),
        ("GLOW12", "pct_off", 12, 0, "C015", "", "", "all", "repurchase_due", *live, 1, 0),
        ("FESTIVE30", "pct_off", 30, 999, "C011", "", "", "all", "festival", *live, 1, 0),
        ("PHONE1500", "flat_off", 1500, 9999, "C009", "", "", "all", "price_drop", *live, 1, 1),
        ("WELCOME", "free_delivery", 0, 0, "", "", "", "new_users", "win_back", *live, 1, 0),
        ("CHAI8", "pct_off", 8, 0, "C018", "", "", "all", "repurchase_due", *live, 1, 0),
        ("FITKIT", "bundle", 15, 0, "", yoga["product_id"], wear["product_id"], "all", "post_purchase", *live, 1, 0),
        ("MEGA50", "pct_off", 50, 0, "", "", "", "all", "cart_abandon", *live, 1, 0),        # too big -> blocked
        ("SALEAUG", "pct_off", 10, 0, "", "", "", "all", "festival",
         "2026-08-10T00:00:00", "2026-08-15T23:59:59", 1, 0),                                 # expired
        ("OLDDROP", "pct_off", 10, 0, "", "", "", "all", "price_drop", *live, 0, 0),          # inactive
        ("MISSYOU", "points", 100, 0, "", "", "", "all", "win_back", *live, 1, 0),
    ]
    rows = []
    for n, s in enumerate(spec, start=1):
        (code, otype, value, min_cart, cat, prod, bundle, seg, trig, start, end, active, approval) = s
        rows.append({"offer_id": f"OF{n:04d}", "code": code, "offer_type": otype, "value": value,
                      "min_cart_value": min_cart, "category_id": cat, "product_id": prod,
                      "bundle_product_id": bundle, "segment": seg, "trigger_type": trig, "max_uses_per_user": 1,
                      "start_at": start, "expires_at": end, "is_active": active, "requires_approval": approval})
    return rows


def build_offer_side_tables(users: list[dict], hidden: list[dict], offers: list[dict]) -> dict[str, list[dict]]:
    profiles = {h["user_id"]: h for h in hidden}
    consent, links, rankings, sends = [], [], [], []
    for user in users:
        uid = user["user_id"]
        opted = 0 if user["is_minor"] else int(uid in ("U00001", "U00002", "U00003", "U00004", "U00050")
                                                or rng.random() < 0.6)
        prefs = profiles[uid]["preferred_categories"].split("|")
        consent.append({"user_id": uid, "opted_in": opted, "quiet_start": "22:00", "quiet_end": "08:00",
                        "preferred_categories": "|".join(prefs[:1]) if opted and rng.random() < 0.5 else "",
                        "max_offers_per_week": 3})
    for n, uid in enumerate(["U00001", "U00002", "U00003", "U00004"], start=1):
        linked = uid != "U00004"  # U00004 got a link but never pressed Start in Telegram
        links.append({"user_id": uid, "chat_id": f"90000000{n}" if linked else "",
                      "link_token": f"{rng.getrandbits(48):012x}",
                      "linked_at": f"2026-08-0{n}T18:00:00" if linked else "", "active": int(linked)})
    live = [o for o in offers if o["is_active"] and o["expires_at"] > "2026-09-30" and o["value"] <= 40]
    for uid in [f"U{n:05d}" for n in range(1, 11)]:
        picks = rng.sample(live, 3)
        for rank, o in enumerate(sorted(picks, key=lambda _: rng.random()), start=1):
            rankings.append({"user_id": uid, "offer_id": o["offer_id"], "score": round(1 - rank * 0.2, 2),
                             "rank": rank, "created_at": "2026-09-30T09:00:00"})
    statuses = ["sent", "opened", "clicked", "redeemed", "opened", "sent", "blocked", "failed",
                "clicked", "sent", "opened", "queued"]
    opted_users = [c["user_id"] for c in consent if c["opted_in"]]
    for n, status in enumerate(statuses, start=1):
        uid = opted_users[n % len(opted_users)]
        o = rng.choice(live)
        when = datetime(2026, 9, 1, 10) + timedelta(days=2 * n, hours=rng.randint(0, 9))
        sends.append({"send_id": f"SN{n:06d}", "user_id": uid, "offer_id": o["offer_id"],
                      "trigger_type": o["trigger_type"],
                      "message": f"Hi! Use code {o['code']} on your next order. Valid till {o['expires_at'][:10]}.",
                      "channel": "telegram", "status": status,
                      "sent_at": "" if status in ("queued", "blocked") else iso(when),
                      "updated_at": iso(when + timedelta(hours=2))})
    return {"user_consent": consent, "telegram_links": links, "offer_rankings": rankings, "offer_sends": sends}


# ---------------------------------------------------------------------------


def write_csv(path: Path, header: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    categories = build_categories()
    products = build_products()
    users, hidden = build_users()
    sim = simulate(users, hidden, products)
    tables = finalize(sim, products)
    offers = build_offers(products)
    tables.update(build_offer_side_tables(users, hidden, offers))
    tables.update({"categories": categories, "products": products, "users": users, "offers": offers,
                   "audit_log": [], "id_map": []})

    for table, rows in tables.items():
        write_csv(OUT / f"{table}.csv", columns(table), rows)
    write_csv(OUT / "ground_truth" / "user_hidden_profile.csv",
              ["user_id", "preferred_categories", "price_sensitivity", "activity_level", "is_gifter"], hidden)
    write_csv(OUT / "ground_truth" / "product_relations.csv",
              ["product_id", "related_product_id", "relation"], build_relations(products))

    print(f"Sample data written to {OUT}")
    for table in sorted(tables):
        print(f"  {table:<15} {len(tables[table]):>5} rows")


if __name__ == "__main__":
    main()
