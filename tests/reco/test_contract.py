"""Every `reco` contract function works and returns RecItem / RankedOffer / UserContext with reasons."""

from common.db import read_table
from common.schemas import RankedOffer, RecItem, UserContext


def _offers(db):
    """Offers from the test DB itself (not the shared app.db, which other phases may reload)."""
    return read_table("offers", db).to_dict("records")


def _check_items(items, k):
    assert isinstance(items, list) and 0 < len(items) <= k
    assert all(isinstance(r, RecItem) for r in items)
    assert all(r.reason.strip() and r.source for r in items), [r for r in items if not r.reason.strip()]
    assert len({r.product_id for r in items}) == len(items), "duplicates in list"


def test_get_recommendations(service):
    recs = service.get_recommendations("U00001", k=10)
    _check_items(recs, 10)
    assert len(recs) == 10
    assert recs[0].score == 1.0  # scores are scaled so the best item is 1.0


def test_get_similar(service):
    _check_items(service.get_similar("P00001", k=5), 5)
    assert service.get_similar("P_DOES_NOT_EXIST") == []


def test_get_complements(service):
    _check_items(service.get_complements("P00001", k=5), 5)


def test_get_trending(service, engine):
    _check_items(service.get_trending(k=5), 5)
    electronics = service.get_trending(k=10, category_id="C001")  # parent category -> its children
    _check_items(electronics, 10)
    cats = {engine.items.at[r.product_id, "category_id"] for r in electronics}
    assert cats <= {"C009", "C010"}


def test_rank_offers_for_user(service, reco_db):
    offers = _offers(reco_db)
    ranked = service.rank_offers_for_user("U00001", offers)
    assert len(ranked) == len(offers)
    assert all(isinstance(r, RankedOffer) and r.reason for r in ranked)
    assert [r.score for r in ranked] == sorted((r.score for r in ranked), reverse=True)


def test_offer_for_cart_item_ranks_first(service, add_event, reco_db):
    # OF0016 = FITKIT bundle on P00016. Put P00016 in the cart of a user with no fitness interest.
    add_event("U00002", "P00016", "add_to_cart")
    ranked = service.rank_offers_for_user("U00002", _offers(reco_db))
    assert ranked[0].offer_id == "OF0016"
    assert "cart" in ranked[0].reason


def test_rank_offers_accepts_dicts(service):
    ranked = service.rank_offers_for_user("U00001", [{"offer_id": "X1", "offer_type": "pct_off", "value": 10,
                                                      "category_id": "C010"}])
    assert ranked[0].offer_id == "X1"


def test_build_user_context(service):
    ctx = service.build_user_context("U00001")
    assert isinstance(ctx, UserContext)
    assert ctx.budget_band in ("low", "mid", "high")
    assert 1 <= len(ctx.top_categories) <= 3
    cold = service.build_user_context("U_NOBODY")
    assert cold.budget_band == "mid" and cold.top_categories == []


def test_contract_functions_are_exported():
    import reco

    for name in ("get_recommendations", "get_similar", "get_complements", "get_trending",
                 "rank_offers_for_user", "build_user_context"):
        assert callable(getattr(reco, name))
