"""Post-purchase rules in detail: kind-aware substitutes, the 14-day category block, fresh complements."""

import copy

import pandas as pd

from reco.data import UserHistory
from reco.engine import Recommender
from reco.rerank import _diverse_top_k, rerank

NOW = "2026-09-30T12:00:00"


def _items(rows):
    """A tiny catalog: (product_id, title, category_id, price)."""
    df = pd.DataFrame(rows, columns=["product_id", "title", "category_id", "price"]).set_index("product_id")
    df["brand"] = [t.split()[0] for t in df["title"]]
    return df


def _bare_engine(items: pd.DataFrame) -> Recommender:
    engine = Recommender.__new__(Recommender)  # no training needed for the catalog helpers
    engine.items = items
    return engine


def test_substitutes_need_the_same_kind():
    """Mobile Accessories holds covers AND chargers: owning a charger must not hide covers."""
    rows = [(f"PC{i}", f"Brand{i} Back Cover (for Phone {i})", "C010", 500 + 20 * i) for i in range(6)]
    rows += [(f"PH{i}", f"Brand{i} 20W Fast Charger (Type-C)", "C010", 600 + 20 * i) for i in range(6)]
    engine = _bare_engine(_items(rows))
    assert engine.kinds()["PC0"] == "cover" and engine.kinds()["PH0"] == "charger"
    subs = engine.substitutes_of("PH0")
    assert {"PH1", "PH5"} <= subs
    assert not subs & {f"PC{i}" for i in range(6)}


def test_unclear_title_kinds_fall_back_to_the_category():
    """Books end in the author's name -> no usable kind; every book in the category is one kind."""
    rows = [(f"PB{i}", f"Some Title {i} by Author{i} (Paperback)", "C021", 300) for i in range(12)]
    engine = _bare_engine(_items(rows))
    assert set(engine.kinds()) == {"C021"}
    assert len(engine.substitutes_of("PB0")) == 11


def _cands(product_ids):
    return pd.DataFrame({"product_id": product_ids, "score": [1.0 - i / 100 for i in range(len(product_ids))],
                         "best_source": "als"})


def test_fresh_purchase_hides_the_whole_category_at_any_price(engine):
    """Bought a ₹17k phone 2 days ago: a ₹1.5k phone (outside the 4× substitute band) is hidden too,
    but only for the first 14 days."""
    eng = copy.deepcopy(engine)
    eng.items.loc["P00022", "price"] = 1500  # Samsung Smartphone -> a keypad-phone price
    eng.__dict__.pop("_substitute_cache_v2", None)
    assert "P00022" not in eng.substitutes_of("P00003")
    others = [p for p in eng.items.index if eng.items.at[p, "category_id"] not in ("C009", "C010")][:20]

    def recs(bought_at):
        hist = UserHistory(user_id="U_X", weights={"P00003": 6.0}, purchases={"P00003": bought_at}, now=NOW)
        out = rerank(_cands(["P00022"] + others), hist, eng.taste(hist), eng, 10, {})
        return out["product_id"].tolist()

    assert "P00022" not in recs("2026-09-28T12:00:00")
    assert "P00022" in recs("2026-09-10T12:00:00")  # 20 days later it is just another (cheap) phone


def test_fresh_purchase_puts_its_complements_first(service, engine, add_event):
    add_event("U00001", "P00003", "purchase")
    after = service.get_recommendations("U00001", k=10)
    top = after[:3]
    assert any(r.source == "complement" and "Redmi Note 13" in r.reason for r in top), [r.reason for r in after]


def test_long_tail_reservation_gives_way_before_the_caps():
    """If the only long-tail products sit in an already-full category, keep the caps and drop the reservation."""
    rows = [(f"PA{i}", f"Brand{i} Thing", "CA", 100) for i in range(6)]
    rows += [(f"PB{i}", f"Other{i} Thing", "CB", 100) for i in range(3)]
    rows += [(f"PC{i}", f"Third{i} Thing", "CC", 100) for i in range(3)]
    rows += [(f"PD{i}", f"Fourth{i} Thing", "CD", 100) for i in range(3)]
    items = _items(rows)
    items["is_longtail"] = items.index.isin(["PA3", "PA4", "PA5"])
    df = pd.DataFrame({"product_id": items.index, "score": range(len(items), 0, -1), "source": "als",
                       "reason_type": "als", "anchor": None})
    out = _diverse_top_k(df, items, 10)
    assert len(out) == 10
    assert (items.loc[out["product_id"], "category_id"] == "CA").sum() <= 3
