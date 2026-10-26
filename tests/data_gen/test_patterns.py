"""The planted patterns really are in the data (or in the simulator's behaviour)."""

import numpy as np
import pandas as pd

from data_gen.catalog import build_catalog
from data_gen.offers import build_consent, build_offers
from data_gen.relations import build_relations
from data_gen.simulate import Simulator
from data_gen.users import build_users
from data_gen.utils import make_rngs


def _simulator(n_products=300, n_users=50):
    rngs = make_rngs(1)
    cats, products = build_catalog(n_products, rngs["catalog"])
    rel = build_relations(products, rngs["relations"])
    users, hidden = build_users(n_users, cats, products, rngs["users"], 1)
    offers = build_offers(cats, products, rel, rngs["offers"])
    consent = build_consent(users, hidden, rngs["consent"])
    sim = Simulator(products, cats, users, hidden, rel, offers, consent, 0.3, rngs["simulate"])
    return sim, products


def _share(sim, st, sub, wanted, day, n=3000):
    picks = [sim._pick_product(st, sub, day) for _ in range(n)]
    return np.isin(picks, list(wanted)).mean()


def test_buying_lowers_substitutes_and_raises_complements():
    sim, products = _simulator()
    sim._prepare_day(100)
    # A non-consumable product that has both substitutes and complements.
    p = next(i for i in range(len(products)) if sim.subst.get(i) and sim.comps.get(i) and not sim.consumable[i])
    st = sim.state[0]
    st.minor = False
    subs, comps = set(sim.subst[p]), set(sim.comps[p])
    sub_cat, comp_cat = int(sim.sub[p]), int(sim.sub[next(iter(comps))])
    before_sub = _share(sim, st, sub_cat, subs, 100)
    before_comp = _share(sim, st, comp_cat, comps, 100)
    sim._after_purchase(0, st, p, 100)
    assert _share(sim, st, sub_cat, subs, 101) < 0.5 * before_sub      # substitutes ×0.1
    assert _share(sim, st, comp_cat, comps, 101) > before_comp         # complements ×3
    # ...and the effect wears off.
    assert _share(sim, st, comp_cat, comps, 100 + 30) <= before_comp * 1.3


def test_consumable_purchase_schedules_a_repurchase_or_a_trigger():
    sim, products = _simulator()
    p = int(np.flatnonzero(sim.consumable)[0])
    for u in range(20):
        sim._after_purchase(u, sim.state[u], p, 10)
    planned = [d for d, items in sim.scheduled.items() for kind, _, q in items if kind == "repurchase" and q == p]
    due = [d for d, items in sim.due_triggers.items() for _, q in items if q == p]
    assert len(planned) + len(due) == 20
    days = sim.rep_days[p]
    assert all(10 + 0.8 * days - 1 <= d <= 10 + 1.2 * days + 1 for d in planned)


def test_repurchase_gaps_follow_repurchase_days(tiny):
    t, _ = tiny
    p = t["products"].set_index("product_id")
    buys = t["events"][(t["events"]["event_type"] == "purchase")].copy()
    buys = buys[buys["product_id"].map(p["is_consumable"]) == 1]
    buys["day"] = pd.to_datetime(buys["timestamp"].str[:10])
    gaps = buys.sort_values("timestamp").groupby(["user_id", "product_id"])["day"].diff().dt.days.dropna()
    ratio = gaps / buys.loc[gaps.index, "product_id"].map(p["repurchase_days"])
    assert len(ratio) >= 10
    assert 0.7 <= ratio.median() <= 1.3


def test_sale_days_have_more_traffic(tiny):
    t, _ = tiny
    days = t["events"]["timestamp"].str[:10].value_counts()
    sale = days[days.index.isin([f"2026-08-{d}" for d in range(10, 16)])].mean()
    normal = days[~days.index.isin([f"2026-08-{d}" for d in range(10, 16)])].mean()
    assert sale > 1.4 * normal


def test_gift_sessions_browse_outside_usual_categories(tiny):
    t, truth = tiny
    gift = set(t["sessions"].loc[t["sessions"]["is_gift"] == 1, "session_id"])
    assert gift, "expected some gift sessions"
    prefs = dict(zip(truth["user_hidden_profile"]["user_id"],
                     truth["user_hidden_profile"]["preferred_categories"].str.split("|")))
    cat = t["products"].set_index("product_id")["category_id"]
    views = t["events"][(t["events"]["event_type"] == "view") & t["events"]["session_id"].isin(gift)]
    in_taste = np.mean([c in prefs[u] for u, c in zip(views["user_id"], views["product_id"].map(cat))])
    assert in_taste < 0.3


def test_long_tail_popularity(tiny):
    t, _ = tiny
    views = t["events"].loc[t["events"]["event_type"] == "view", "product_id"].value_counts()
    top20 = views.iloc[: int(0.2 * len(t["products"]))].sum() / views.sum()
    assert top20 > 0.5
