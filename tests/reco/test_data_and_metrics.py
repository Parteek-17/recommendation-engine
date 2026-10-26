"""Data prep (reco/data.py) and metric maths (reco/evaluate.py)."""

import math

import pandas as pd
import pytest

from reco.data import (
    build_matrix,
    history_from_frames,
    load_data,
    make_split,
    relevance_labels,
    weighted_interactions,
)
from reco.evaluate import average_precision_at_k, ndcg_at_k, recall_at_k


def _events(rows):
    return pd.DataFrame(rows, columns=["event_id", "user_id", "product_id", "session_id", "event_type",
                                       "timestamp", "source", "search_query"])


def test_load_sample_db(reco_db):
    data = load_data(reco_db)
    assert data.level == "L4" and data.origin == "db"
    assert len(data.events) == 2160
    assert data.products["price"].dtype.kind == "f"  # numbers, not text
    assert data.has("content") and data.has("post_purchase")


def test_contract_split_for_our_data(reco_db):
    split = make_split(load_data(reco_db).events)
    assert (split.train_end, split.valid_start, split.test_end) == ("2026-08-31", "2026-09-01", "2026-09-30")


def test_proportional_split_for_external_data():
    ev = _events([[f"E{i}", "U1", "P1", "S1", "view", f"2019-{m:02d}-01T10:00:00", "home", None]
                  for i, m in enumerate(range(1, 13))])
    split = make_split(ev)
    assert "2019-01-01" < split.train_end < split.valid_start <= split.valid_end < split.test_start <= split.test_end
    assert split.test_end == "2019-12-01"


def test_event_weights_and_gift_sessions_excluded():
    ev = _events([
        ["E1", "U1", "P1", "S1", "view", "2026-05-01T10:00:00", "home", None],
        ["E2", "U1", "P1", "S1", "purchase", "2026-05-01T10:05:00", "home", None],
        ["E3", "U1", "P2", "S2", "add_to_cart", "2026-05-02T10:00:00", "home", None],  # gift session
        ["E4", "U1", None, "S1", "search", "2026-05-01T09:59:00", "search", "phone"],
    ])
    sessions = pd.DataFrame({"session_id": ["S1", "S2"], "is_gift": [0, 1]})
    personal = weighted_interactions(ev, sessions, personal=True)
    assert personal.set_index("product_id")["weight"].to_dict() == {"P1": 7.0}  # view 1 + purchase 6
    everything = weighted_interactions(ev, sessions, personal=False)
    assert set(everything["product_id"]) == {"P1", "P2"}


def test_build_matrix_shape():
    inter = pd.DataFrame({"user_id": ["U1", "U2"], "product_id": ["P1", "P3"], "weight": [2.0, 6.0]})
    im = build_matrix(inter, ["P1", "P2", "P3"])
    assert im.matrix.shape == (2, 3)
    assert im.matrix[im.user_index["U2"], im.item_index["P3"]] == 6.0


def test_history_cart_replay_and_purchases():
    ev = _events([
        ["E1", "U1", "P1", "S1", "add_to_cart", "2026-05-01T10:00:00", "home", None],
        ["E2", "U1", "P2", "S1", "add_to_cart", "2026-05-01T10:01:00", "home", None],
        ["E3", "U1", "P1", "S1", "remove_from_cart", "2026-05-01T10:02:00", "home", None],
        ["E4", "U1", "P2", "S1", "purchase", "2026-05-01T10:03:00", "home", None],
        ["E5", "U1", "P3", "S1", "add_to_cart", "2026-05-01T10:04:00", "home", None],
    ])
    empty = pd.DataFrame(columns=["order_id", "user_id", "order_date", "product_id"])
    hist = history_from_frames("U1", ev, pd.DataFrame(columns=["session_id", "is_gift"]), empty, empty,
                               pd.DataFrame(columns=["user_id"]))
    assert hist.cart == {"P3"}
    assert set(hist.purchases) == {"P2"}
    assert hist.now == "2026-05-01T10:04:00"


def test_relevance_labels_keep_strongest_action():
    ev = _events([["E1", "U1", "P1", "S1", "view", "t", "home", None],
                  ["E2", "U1", "P1", "S1", "add_to_cart", "t", "home", None]])
    assert relevance_labels(ev)["grade"].tolist() == [4]


def test_metrics_on_a_known_list():
    recs, relevant = ["A", "B", "C", "D"], {"B", "D", "Z"}
    assert recall_at_k(recs, relevant) == pytest.approx(2 / 3)
    ideal = 1 + 1 / math.log2(3) + 1 / math.log2(4)
    assert ndcg_at_k(recs, relevant) == pytest.approx((1 / math.log2(3) + 1 / math.log2(5)) / ideal)
    assert average_precision_at_k(recs, relevant) == pytest.approx((1 / 2 + 2 / 4) / 3)
    assert recall_at_k(["A"], set()) == 0.0
    assert ndcg_at_k(["B", "D", "Z"], relevant) == pytest.approx(1.0)
