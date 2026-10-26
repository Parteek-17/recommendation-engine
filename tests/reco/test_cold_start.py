"""Cold-start users: popularity is mixed with what users in the same city tier engage with."""

import pandas as pd

from reco.candidates.popular import PopularityModel


def test_new_users_get_what_their_city_tier_buys():
    users = pd.DataFrame({"user_id": ["U1", "U2", "U3", "U4"], "city_tier": [1, 1, 3, 3]})
    rows = [("U1", "P_PRICEY"), ("U2", "P_PRICEY"), ("U3", "P_CHEAP"), ("U4", "P_CHEAP"), ("U4", "P_CHEAP")]
    events = pd.DataFrame({"user_id": [u for u, _ in rows], "product_id": [p for _, p in rows],
                           "event_type": "purchase", "timestamp": "2026-09-01T10:00:00"})
    model = PopularityModel().fit(events, users, "2026-09-30T00:00:00")

    assert model.candidates({"city_tier": 1}, 2).index[0] == "P_PRICEY"
    assert model.candidates({"city_tier": "3.0"}, 2).index[0] == "P_CHEAP"  # same tier, read as text
    assert model.candidates({}, 2).index[0] == "P_CHEAP"  # unknown tier -> plain trending
