"""Shared data shapes (pydantic models) and enums from CLAUDE.md.

Every module passes these objects between each other, so a field name here is a
promise to the whole team. Don't rename fields — add a note in phases/NOTES.md instead.

Pydantic checks types for us: RecItem(product_id="P00001", score="abc", ...) raises
a clear ValidationError instead of failing somewhere far away later.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enums (exact values from the Data Contract).
# They subclass `str`, so EventType.VIEW == "view" is True and they save to the DB as plain text.
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    VIEW = "view"
    CLICK = "click"
    ADD_TO_CART = "add_to_cart"
    REMOVE_FROM_CART = "remove_from_cart"
    WISHLIST = "wishlist"
    PURCHASE = "purchase"
    SEARCH = "search"


class EventSource(str, Enum):
    HOME = "home"
    SEARCH = "search"
    PRODUCT_PAGE = "product_page"
    RECOMMENDATION = "recommendation"
    TELEGRAM = "telegram"


class OfferType(str, Enum):
    PCT_OFF = "pct_off"
    FLAT_OFF = "flat_off"
    FREE_DELIVERY = "free_delivery"
    BUNDLE = "bundle"
    BUY_AGAIN = "buy_again"
    POINTS = "points"


class TriggerType(str, Enum):
    CART_ABANDON = "cart_abandon"
    PRICE_DROP = "price_drop"
    REPURCHASE_DUE = "repurchase_due"
    POST_PURCHASE = "post_purchase"
    WIN_BACK = "win_back"
    FESTIVAL = "festival"


class SendStatus(str, Enum):
    QUEUED = "queued"
    SENT = "sent"
    OPENED = "opened"
    CLICKED = "clicked"
    REDEEMED = "redeemed"
    BLOCKED = "blocked"
    FAILED = "failed"


class Relation(str, Enum):
    """Ground-truth product relation (evaluation only)."""

    COMPLEMENT = "complement"
    SUBSTITUTE = "substitute"


# Implicit-feedback weights used when training (search/remove carry no positive signal).
EVENT_WEIGHTS: dict[str, int] = {
    "view": 1,
    "click": 2,
    "wishlist": 3,
    "add_to_cart": 4,
    "purchase": 6,
}

# Time split for training / evaluation (inclusive dates).
DATA_START = "2026-04-01"
DATA_END = "2026-09-30"
TRAIN_END = "2026-08-31"
VALID_START, VALID_END = "2026-09-01", "2026-09-15"
TEST_START, TEST_END = "2026-09-16", "2026-09-30"

# ---------------------------------------------------------------------------
# Module-contract models
# ---------------------------------------------------------------------------


class RecItem(BaseModel):
    """One recommended product. `reason` is shown to the user (explainable recs)."""

    product_id: str
    score: float
    reason: str
    source: str = Field(description="Which model produced it, e.g. 'popularity', 'item_cf', 'als', 'content', 'ranker'")


class Offer(BaseModel):
    """One row of the `offers` table."""

    offer_id: str
    code: str
    offer_type: OfferType
    value: float = Field(description="pct for pct_off, ₹ for flat_off, points for points, else 0")
    min_cart_value: float = 0.0
    category_id: str | None = None
    product_id: str | None = None
    bundle_product_id: str | None = None
    segment: str | None = Field(default=None, description="Target segment, e.g. 'all', 'tier3', 'new_users'")
    trigger_type: TriggerType | None = None
    max_uses_per_user: int = 1
    start_at: datetime
    expires_at: datetime
    is_active: bool = True
    requires_approval: bool = False


class RankedOffer(BaseModel):
    """An offer ranked for one user by the rec engine."""

    offer_id: str
    score: float
    reason: str


class UserContext(BaseModel):
    """A short summary of a user, used for offers and LLM wording (no raw personal data)."""

    user_id: str
    budget_band: str = Field(description="'low' | 'mid' | 'high'")
    top_categories: list[str] = Field(default_factory=list, description="category_ids, most liked first")
    trigger_type: TriggerType | None = None
    is_gift_mode: bool = False


class ChatReply(BaseModel):
    """What the chat agent sends back. product_ids must exist in the DB (grounding)."""

    text: str
    product_ids: list[str] = Field(default_factory=list)
    buttons: list[str] = Field(default_factory=list)


class GuardrailResult(BaseModel):
    """Answer from any guardrail check: allowed or not, and why (logged in audit_log)."""

    allowed: bool
    reason: str = ""
