"""Unit tests for ingest/transforms.py (all converters must survive bad values)."""

import pandas as pd

from ingest import transforms as T


def test_unix_times_become_local_iso():
    # 2025-09-01 08:00:00 UTC = 13:30 in Asia/Kolkata
    assert T.unix_ms(pd.Series([1756713600000])).iloc[0] == "2025-09-01T13:30:00"
    assert T.unix_s(pd.Series(["1756713600"])).iloc[0] == "2025-09-01T13:30:00"
    assert T.unix_ms(pd.Series(["oops"])).iloc[0] is None


def test_dates_are_day_first_and_timezone_aware():
    out = T.date(pd.Series(["12-08-2026", "2026-08-12 19:30", "2026-08-12T14:00:00Z", "", "garbage"]))
    assert out.tolist()[:3] == ["2026-08-12T00:00:00", "2026-08-12T19:30:00", "2026-08-12T19:30:00"]
    assert out.iloc[3] is None and out.iloc[4] is None
    assert T.date(pd.Series(["08/12/2026"]), format="%m/%d/%Y").iloc[0] == "2026-08-12T00:00:00"


def test_money():
    assert T.parse_money("₹1,299") == 1299.0
    assert T.parse_money("Rs. 2,799") == 2799.0
    assert T.parse_money("$12.99", rate=84) == round(12.99 * 84, 2)
    assert T.parse_money("1,299 - 1,499") == 1299.0
    assert T.parse_money(499) == 499.0
    assert T.parse_money(None) is None and T.parse_money("free") is None


def test_rating_rescale():
    assert T.rating(pd.Series([10, 1, 5.5]), scale=[1, 10]).tolist() == [5.0, 1.0, 3.0]
    assert T.rating(pd.Series([0, 5]), scale=[0, 5]).tolist() == [1.0, 5.0]
    assert T.rating(pd.Series([4.5, "x"])).iloc[0] == 4.5


def test_event_map_unknown_becomes_none():
    out = T.event_map(pd.Series(["view", "AddToCart", "share", None]),
                      mapping={"view": "view", "addtocart": "add_to_cart"})
    assert out.tolist() == ["view", "add_to_cart", None, None]


def test_paths_and_lists():
    assert T.list_to_path(pd.Series([["Electronics", "Phones"]])).iloc[0] == "Electronics > Phones"
    assert T.list_to_path(pd.Series(['["A", "B"]'])).iloc[0] == "A > B"
    assert T.list_to_path(pd.Series([[]])).iloc[0] is None
    assert T.split_path(pd.Series(["Clothing>Men >T-shirts"]), sep=">").iloc[0] == "Clothing > Men > T-shirts"
    assert T.join_pipe(pd.Series([["5000mAh | fast", "6.5 inch"]])).iloc[0] == "5000mAh fast|6.5 inch"
    assert T.join_text(pd.Series([["A <b>great</b>", "phone."]])).iloc[0] == "A great phone."


def test_strip_html_and_length_limit():
    assert T.strip_html("<p>Works <b>perfectly</b> !</p>&amp; more") == "Works perfectly! & more"
    assert len(T.strip_html("x" * 5000)) == T.MAX_TEXT
    assert T.strip_html(None) == ""
