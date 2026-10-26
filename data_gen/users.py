"""Step 3 — Users and their HIDDEN taste profiles.

    users, hidden = build_users(n_users, categories, products, rng, seed)

`users` is a normal table (demographics). `hidden` is GROUND TRUTH that only the simulation
and the evaluation may see: 2-3 preferred sub-categories, price_sensitivity (0 = premium
buyer, 1 = bargain hunter), activity_level and is_gifter. Models must never read it.

Internal columns on `hidden` (not saved): _pref_weights (how interest splits across the
preferred categories) and _sessions_per_month.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
from faker import Faker

from data_gen import config as C
from data_gen.templates import SUBCATEGORIES
from data_gen.utils import WINDOW_END, to_date

TIER1 = ["Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Chennai", "Kolkata", "Pune", "Ahmedabad"]
TIER2 = ["Jaipur", "Lucknow", "Indore", "Chandigarh", "Ludhiana", "Coimbatore", "Nagpur", "Surat", "Bhopal",
         "Kochi", "Vadodara", "Visakhapatnam", "Patna", "Amritsar", "Dehradun", "Mysuru", "Nashik", "Ranchi"]


def tier3_cities(seed: int, n: int = 60) -> list[str]:
    """Smaller towns from Faker's Indian locale (anything that isn't a Tier-1/2 city above)."""
    fake = Faker("en_IN")
    fake.seed_instance(seed)
    known = set(TIER1) | set(TIER2)
    out: list[str] = []
    for _ in range(n * 20):
        city = fake.city()
        if city not in known and city not in out:
            out.append(city)
        if len(out) >= n:
            break
    return out or ["Bathinda", "Moga", "Hoshiarpur", "Jhansi", "Bareilly", "Hubli"]


def category_weights(categories: pd.DataFrame, products: pd.DataFrame) -> tuple[list[str], np.ndarray, dict]:
    """Leaf categories that have products, how popular each is, and each one's parent.

    Our own sub-categories use the popularity weights from templates.py; categories from an
    external catalog (ingest --fill-missing) are weighted by how many products they have.
    """
    counts = products["category_id"].value_counts()
    subs = sorted(counts.index)
    weights = np.array([SUBCATEGORIES[s]["weight"] if s in SUBCATEGORIES else counts[s] for s in subs], float)
    parent_of = dict(zip(categories["category_id"], categories["parent_id"].fillna("")))
    return subs, weights / weights.sum(), {s: parent_of.get(s, "") for s in subs}


def _pick(rng: np.random.Generator, options: dict) -> str:
    keys = list(options)
    return keys[rng.choice(len(keys), p=np.array(list(options.values())) / sum(options.values()))]


def _pick_preferences(rng: np.random.Generator, subs: list[str], weights: np.ndarray,
                      parent: dict[str, str]) -> list[str]:
    """2-3 sub-categories. Half the time the next one shares a parent (Mobiles + Accessories)."""
    k = min(len(subs), int(rng.integers(C.PREF_CATEGORIES[0], C.PREF_CATEGORIES[1] + 1)))
    prefs = [subs[rng.choice(len(subs), p=weights)]]
    while len(prefs) < k:
        siblings = [s for s in subs if s not in prefs and parent[s] and parent[s] == parent[prefs[-1]]]
        if siblings and rng.random() < C.PREF_SAME_PARENT_PROB:
            prefs.append(siblings[rng.integers(len(siblings))])
            continue
        w = np.array([0.0 if s in prefs else weights[i] for i, s in enumerate(subs)])
        prefs.append(subs[rng.choice(len(subs), p=w / w.sum())])
    return prefs


def _signup_date(rng: np.random.Generator) -> str:
    """10% joined in the last month (cold-start users); the rest any time before that."""
    if rng.random() < C.COLD_START_USER_RATE:
        return (WINDOW_END - timedelta(days=int(rng.integers(0, C.COLD_START_USER_DAYS)))).isoformat()
    oldest = to_date(C.OLDEST_SIGNUP)
    last = WINDOW_END - timedelta(days=C.COLD_START_USER_DAYS)
    return (oldest + timedelta(days=int(rng.integers(0, (last - oldest).days + 1)))).isoformat()


def build_users(n_users: int, categories: pd.DataFrame, products: pd.DataFrame, rng: np.random.Generator,
                seed: int = C.SEED) -> tuple[pd.DataFrame, pd.DataFrame]:
    subs, weights, parent = category_weights(categories, products)
    cities = {1: TIER1, 2: TIER2, 3: tier3_cities(seed)}
    users, hidden = [], []
    for n in range(1, n_users + 1):
        uid = f"U{n:05d}"
        age = _pick(rng, C.AGE_GROUPS)
        tier = int(_pick(rng, C.CITY_TIERS))
        users.append({
            "user_id": uid, "signup_date": _signup_date(rng), "age_group": age, "gender": _pick(rng, C.GENDERS),
            "city": cities[tier][rng.integers(len(cities[tier]))], "city_tier": tier,
            "device": _pick(rng, C.DEVICES), "is_minor": int(age == "13-17"),
        })
        prefs = _pick_preferences(rng, subs, weights, parent)
        level = _pick(rng, {k: v[0] for k, v in C.ACTIVITY_LEVELS.items()})
        lo, hi = C.ACTIVITY_LEVELS[level][1]
        sensitivity = rng.beta(*C.PRICE_SENS_BETA) + (C.TIER3_SENS_SHIFT if tier == 3 else 0.0)
        hidden.append({
            "user_id": uid,
            "preferred_categories": "|".join(prefs),
            "price_sensitivity": round(float(min(1.0, sensitivity)), 2),
            "activity_level": level,
            "is_gifter": int(rng.random() < C.GIFTER_RATE),
            "_pref_weights": rng.dirichlet([C.PREF_DIRICHLET_ALPHA] * len(prefs)).tolist(),
            "_sessions_per_month": float(rng.uniform(lo, hi)),
        })
    return pd.DataFrame(users), pd.DataFrame(hidden)
