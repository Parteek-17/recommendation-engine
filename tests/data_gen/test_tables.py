"""Checks on individual generated tables (catalog, users, relations, events, offers)."""

import numpy as np
import pandas as pd

from common.schemas import DATA_END, DATA_START
from data_gen import config as C
from data_gen.templates import PARENTS


def test_catalog_shape(tiny):
    t, _ = tiny
    cats, p = t["categories"], t["products"]
    assert (cats["parent_id"] == "").sum() == 8 and (cats["parent_id"] != "").sum() == 25
    assert len(p) == C.SIZES["tiny"]["products"]
    assert p["product_id"].is_unique and p["title"].is_unique
    assert (p["mrp"] >= p["price"]).all()
    assert p["avg_rating"].between(1, 5).all()


def test_prices_inside_parent_ranges(tiny):
    t, _ = tiny
    parent = t["categories"].set_index("category_id")["parent_id"]
    for _, row in t["products"].iterrows():
        lo, hi = C.PARENT_PRICE_RANGES[PARENTS[parent[row["category_id"]]]]
        assert lo <= row["price"] <= hi, row["title"]


def test_consumables_cold_start_and_age_restriction(tiny):
    p = tiny[0]["products"]
    cons = p[p["is_consumable"] == 1]
    assert len(cons) > 0 and cons["repurchase_days"].between(15, 60).all()
    assert p.loc[p["is_consumable"] == 0, "repurchase_days"].isna().all()
    late = (p["launch_date"] > "2026-08-19").mean()
    assert 0.03 <= late <= 0.20                       # ~10% cold-start items
    assert 0 < p["age_restricted"].sum() <= 0.05 * len(p)


def test_users_and_hidden_profiles(tiny):
    t, truth = tiny
    users, hidden = t["users"], truth["user_hidden_profile"]
    assert (users["is_minor"] == (users["age_group"] == "13-17").astype(int)).all()
    assert hidden["price_sensitivity"].between(0, 1).all()
    n_prefs = hidden["preferred_categories"].str.split("|").str.len()
    assert n_prefs.between(2, 3).all()
    late_signup = (users["signup_date"] >= "2026-09-01").mean()
    assert 0.03 <= late_signup <= 0.20                # ~10% cold-start users
    assert set(hidden["activity_level"]) <= {"light", "regular", "heavy"}


def test_relations(tiny):
    t, truth = tiny
    rel, p = truth["product_relations"], t["products"].set_index("product_id")
    subs = rel[rel["relation"] == "substitute"]
    a, b = p.loc[subs["product_id"]], p.loc[subs["related_product_id"]]
    assert (a["category_id"].to_numpy() == b["category_id"].to_numpy()).all()
    assert (abs(a["price"].to_numpy() - b["price"].to_numpy()) <= 0.3 * a["price"].to_numpy() + 1e-9).all()
    comps = rel[rel["relation"] == "complement"]
    phones = p.index[p["_kind"] == "smartphone"]
    phone_comps = comps[comps["product_id"].isin(phones)]
    assert len(phone_comps) and set(p.loc[phone_comps["related_product_id"], "_kind"]) <= {
        "phone_cover", "charger", "earbuds", "screen_guard", "power_bank"}
    assert comps.groupby("product_id").size().max() <= 5


def test_events_are_sane(tiny):
    t, _ = tiny
    ev = t["events"]
    assert ev["timestamp"].is_monotonic_increasing
    assert ev["timestamp"].min() >= DATA_START and ev["timestamp"].max() <= DATA_END + "T23:59:59"
    search = ev[ev["event_type"] == "search"]
    assert (search["product_id"] == "").all() and (search["search_query"] != "").all()
    assert (ev.loc[ev["event_type"] != "search", "product_id"] != "").all()
    # Nobody interacts with a product before it launched.
    launch = t["products"].set_index("product_id")["launch_date"]
    with_p = ev[ev["product_id"] != ""]
    assert (with_p["timestamp"].str[:10] >= with_p["product_id"].map(launch)).all()
    # ...or before they signed up.
    signup = t["users"].set_index("user_id")["signup_date"]
    assert (ev["timestamp"].str[:10] >= ev["user_id"].map(signup)).all()


def test_minors_never_see_age_restricted_products(tiny):
    t, _ = tiny
    minors = set(t["users"].loc[t["users"]["is_minor"] == 1, "user_id"])
    adult = set(t["products"].loc[t["products"]["age_restricted"] == 1, "product_id"])
    ev = t["events"]
    assert not (ev["user_id"].isin(minors) & ev["product_id"].isin(adult)).any()


def test_taste_drives_browsing(tiny):
    t, truth = tiny
    prefs = dict(zip(truth["user_hidden_profile"]["user_id"],
                     truth["user_hidden_profile"]["preferred_categories"].str.split("|")))
    cat = t["products"].set_index("product_id")["category_id"]
    views = t["events"][t["events"]["event_type"] == "view"]
    share = np.mean([c in prefs[u] for u, c in zip(views["user_id"], views["product_id"].map(cat))])
    assert 0.6 <= share <= 0.9                          # ~80% taste, ~20% exploration/noise


def test_orders_match_purchases(tiny):
    t, _ = tiny
    buys = t["events"][t["events"]["event_type"] == "purchase"]
    items = t["order_items"]
    assert items["quantity"].sum() >= len(buys)          # consumables may have quantity > 1
    totals = (items["quantity"] * items["price_paid"]).groupby(items["order_id"]).sum().round(2)
    orders = t["orders"].set_index("order_id")["total_amount"]
    assert np.allclose(orders.loc[totals.index], totals)
    assert t["reviews"]["rating"].between(1, 5).all()


def test_offers_consent_and_sends(tiny):
    t, _ = tiny
    offers, consent, sends = t["offers"], t["user_consent"], t["offer_sends"]
    assert 40 <= len(offers) <= 70
    assert offers["code"].is_unique
    from common.schemas import OfferType, TriggerType
    assert set(offers["offer_type"]) == {e.value for e in OfferType}
    assert set(offers["trigger_type"]) == {e.value for e in TriggerType}
    minors = t["users"].set_index("user_id")["is_minor"]
    assert (consent.loc[consent["user_id"].map(minors) == 1, "opted_in"] == 0).all()
    assert 0.4 <= consent["opted_in"].mean() <= 0.7
    if len(sends):
        opted = set(consent.loc[consent["opted_in"] == 1, "user_id"])
        assert sends["user_id"].isin(opted).all()
        assert (sends.loc[sends["status"] == "blocked", "sent_at"] == "").all()
        hours = pd.to_datetime(sends.loc[sends["sent_at"] != "", "sent_at"]).dt.hour
        assert hours.between(10, 21).all()               # never in quiet hours
