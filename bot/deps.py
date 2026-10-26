"""Functions the bot borrows from other modules — real ones if built, stubs otherwise.

Phases run in parallel, so `llm`, `reco` or `offers` may not exist yet. As soon as a
module exports its contract functions from its `__init__.py`, the import below
succeeds and the bot uses the real thing automatically (no code change here).
"""

from __future__ import annotations

try:
    from llm import chat
except ImportError:
    from common.stubs import chat

try:
    from reco import build_user_context, get_complements, get_recommendations
except ImportError:
    from common.stubs import build_user_context, get_complements, get_recommendations

try:
    from offers import revalidate
except ImportError:
    from common.stubs import revalidate

__all__ = ["chat", "build_user_context", "get_complements", "get_recommendations", "revalidate"]
