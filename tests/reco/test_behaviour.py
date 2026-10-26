"""The "Done when" behaviours: post-purchase, cold start, safety rules, user control, robustness."""

import shutil
from collections import Counter

from reco.data import UserHistory, load_data
from reco.engine import Recommender
from tests.reco.conftest import NEW_PRODUCT

PHONES = "C009"


def _cats(engine, recs):
    return [engine.items.at[r.product_id, "category_id"] for r in recs]


def test_post_purchase_no_more_phones_but_complements(service, engine, add_event):
    """U00001 likes phones. After buying one: no other phones in the top-10, complements appear."""
    before = service.get_recommendations("U00001", k=10)
    assert PHONES in _cats(engine, before), "precondition: a phone fan sees phones"

    add_event("U00001", "P00003", "purchase")  # Redmi Note 13 5G
    after = service.get_recommendations("U00001", k=10)
    assert PHONES not in _cats(engine, after)
    complements = [r for r in after if r.reason.startswith("Goes well with your Redmi Note 13")]
    assert complements, [r.reason for r in after]


def test_bought_item_itself_is_not_recommended_again(service, add_event):
    add_event("U00003", "P00016", "purchase")  # non-consumable
    assert "P00016" not in {r.product_id for r in service.get_recommendations("U00003", k=10)}


def test_cold_start_user_gets_sensible_recs(service, engine):
    recs = service.get_recommendations("U_BRAND_NEW", k=10)
    assert len(recs) == 10
    assert all(engine.items.at[r.product_id, "stock_qty"] > 0 for r in recs)
    assert all(r.reason for r in recs)


def test_new_product_without_interactions(service, engine):
    """A product launched yesterday with zero clicks still gets similar items (via its text) and complements."""
    pid = NEW_PRODUCT["product_id"]
    assert engine.items.at[pid, "popularity"] == 0
    similar = service.get_similar(pid, k=5)
    assert similar and all(r.source == "content" for r in similar)
    assert engine.items.at[similar[0].product_id, "category_id"] == "C010"
    # and it can be recommended to someone whose taste matches its text
    hist = UserHistory(user_id="U_X", weights={"P00007": 6.0, "P00086": 4.0}, now="2026-09-30T12:00:00")
    cands = engine.score_candidates(hist, "content")
    assert pid in cands["product_id"].tolist()


def test_out_of_stock_never_recommended(service, engine):
    out_of_stock = set(engine.items.index[engine.items["stock_qty"] == 0])
    assert out_of_stock, "precondition: sample data has out-of-stock products"
    for uid in ["U00001", "U00002", "U00003", "U00010", "U00020", "U_NEW"]:
        assert not out_of_stock & {r.product_id for r in service.get_recommendations(uid, k=10)}
    assert not out_of_stock & {r.product_id for r in service.get_trending(k=50)}


def test_minor_never_sees_age_restricted(service, engine):
    restricted = set(engine.items.index[engine.items["age_restricted"] == 1])
    recs = service.get_recommendations("U00007", k=30)  # U00007 is_minor = 1
    assert not restricted & {r.product_id for r in recs}


def test_max_three_per_category_and_brand(service, engine):
    recs = service.get_recommendations("U00001", k=10)
    assert max(Counter(_cats(engine, recs)).values()) <= 3
    assert max(Counter(engine.items.at[r.product_id, "brand"] for r in recs).values()) <= 3


def test_long_tail_slots(service, engine):
    recs = service.get_recommendations("U00005", k=10)
    assert sum(bool(engine.items.at[r.product_id, "is_longtail"]) for r in recs) >= 2


def test_no_long_tail_slots_for_cold_users(engine):
    """New users get the in-stock bestsellers in popularity order (tuned on the validation window)."""
    cold = UserHistory(user_id="U_BRAND_NEW")
    with_rule = engine.recommend_frame(cold, 10)["product_id"].tolist()
    without_rule = engine.recommend_frame(cold, 10, disabled_rules=frozenset({"long_tail"}))["product_id"].tolist()
    assert with_rule == without_rule


def test_budget_band_is_a_hard_filter(service, engine):
    recs = service.get_recommendations("U00001", k=10, context={"budget_band": "low"})
    assert recs and all(engine.items.at[r.product_id, "price_pct"] <= 0.4 for r in recs)


def test_gift_mode_ignores_personal_history(service, engine):
    personal = service.get_recommendations("U00001", k=10)
    gift = service.get_recommendations("U00001", k=10, context={"is_gift_mode": True, "top_categories": ["C023"]})
    assert all(r.reason.startswith("Popular gift pick") for r in gift)
    assert "C023" in _cats(engine, gift)
    assert {r.product_id for r in gift} != {r.product_id for r in personal}


def test_reasons_mention_real_products(service, engine):
    titles = set(engine.items["title"].dropna())
    for r in service.get_recommendations("U00001", k=10):
        if r.reason.startswith("Because you"):
            assert any(t in r.reason for t in titles)


def test_runs_on_events_only_data(tmp_path):
    """L1 data (events only, like many public datasets): no catalog -> content/ranker off, nothing crashes."""
    from common.db import load_csvs_to_db
    from tests.reco.conftest import ROOT

    folder = tmp_path / "l1"
    folder.mkdir()
    shutil.copy(ROOT / "data" / "sample" / "events.csv", folder / "events.csv")
    db = tmp_path / "l1.db"
    load_csvs_to_db(folder, db_path=db)
    data = load_data(db)
    assert data.level == "L1"

    from reco.train import train_serving_engine

    engine = train_serving_engine(db)
    assert engine.content is None and engine.ranker is None and engine.item_cf is not None
    hist = UserHistory(user_id="U00001", weights={"P00001": 6.0})
    recs = engine.recommend(hist, k=5)
    assert len(recs) == 5 and all(r.reason for r in recs)
    assert engine.similar("P00001", 3) and engine.trending(3)
    assert isinstance(engine.complements("P00001", 3), list)


def test_saved_bundle_round_trip(engine, tmp_path):
    from reco.service import _load_bundle, save_engine

    path = tmp_path / "bundle.pkl"
    save_engine(engine, path)
    loaded = _load_bundle(path)
    assert isinstance(loaded, Recommender)
    hist = UserHistory(user_id="U_X", weights={"P00001": 6.0}, now="2026-09-30T12:00:00")
    assert [r.product_id for r in loaded.recommend(hist)] == [r.product_id for r in engine.recommend(hist)]


def test_evaluation_harness_runs(reco_db):
    from reco.data import make_split, relevance_labels, window
    from reco.evaluate import GroundTruth, evaluate_stages

    data = load_data(reco_db)
    split = make_split(data.events)
    past = data.until(split.valid_end)
    engine = Recommender.fit(past)
    labels = relevance_labels(window(data.events, split.test_start, split.test_end))
    results, n_users = evaluate_stages(engine, past, labels, ["popularity", "full", "full-owned"], GroundTruth())
    assert n_users > 0
    for metrics in results.values():
        assert 0 <= metrics["recall"] <= 1 and 0 <= metrics["ndcg"] <= 1 and 0 < metrics["coverage"] <= 1
        assert "pref_cat" in metrics and "sub_leak" in metrics
