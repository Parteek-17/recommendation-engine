"""Recommendation engine (Phase 2). Public functions = the `reco` row of CLAUDE.md → Module Contracts.

Other modules use:  try: from reco import get_recommendations  except ImportError: from common.stubs import ...
"""

from reco.service import (
    build_user_context,
    get_complements,
    get_recommendations,
    get_similar,
    get_trending,
    rank_offers_for_user,
)

__all__ = [
    "get_recommendations",
    "get_similar",
    "get_complements",
    "get_trending",
    "rank_offers_for_user",
    "build_user_context",
]
