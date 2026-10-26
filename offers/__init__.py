"""Offer Engine + Offers API (Phase 3).

Module-contract functions (CLAUDE.md -> Module Contracts -> offers). Exported here so
`from offers import check_eligibility` replaces the stub in common/stubs.py automatically.

The FastAPI app (offers.api) and the generator (offers.generator) are NOT imported here:
using these functions shouldn't load the web server, and `python -m offers.generator`
works without a "found in sys.modules" warning. Import them directly when needed.
"""

from offers.client import fetch_live_offers
from offers.eligibility import check_eligibility
from offers.guardrails import apply_offer_guardrails, revalidate

__all__ = ["fetch_live_offers", "check_eligibility", "apply_offer_guardrails", "revalidate"]
