"""Step 11 — Public API of the reco module (the functions in CLAUDE.md → Module Contracts).

    from reco import get_recommendations
    recs = get_recommendations("U00001", k=10)          # list[RecItem], each with a reason

How it works:
- The trained models live in models/reco_bundle.pkl (made by `python -m reco.train`).
  If the file is missing or was trained on a different DATA_SOURCE, we train on the fly
  (seconds on sample data) and save it.
- The USER's history is read fresh from app.db on every call, so a purchase logged a
  second ago already changes the recommendations (post-purchase intelligence).
"""

from __future__ import annotations

import pickle
from pathlib import Path

from common.config import PROJECT_ROOT, settings
from common.logging_utils import get_logger
from common.schemas import Offer, RankedOffer, RecItem, UserContext
from reco.data import load_user_history
from reco.engine import Recommender
from reco.offer_ranking import build_user_context as _build_context
from reco.offer_ranking import rank_offers

log = get_logger(__name__)

MODEL_PATH = PROJECT_ROOT / "models" / "reco_bundle.pkl"
BUNDLE_VERSION = 1

_engine: Recommender | None = None
_db_path: Path | None = None


def save_engine(engine: Recommender, path: Path = MODEL_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    bundle = {"version": BUNDLE_VERSION, "data_source": settings.DATA_SOURCE, "engine": engine}
    with open(path, "wb") as fh:
        pickle.dump(bundle, fh)
    log.info("Saved models to %s", path)


def _load_bundle(path: Path) -> Recommender | None:
    try:
        with open(path, "rb") as fh:
            bundle = pickle.load(fh)
    except Exception as exc:  # corrupt file, library upgrade...
        log.warning("Could not load %s (%s) — retraining.", path, exc)
        return None
    if bundle.get("version") != BUNDLE_VERSION or bundle.get("data_source") != settings.DATA_SOURCE:
        log.warning("Saved models are for another DATA_SOURCE/version — retraining.")
        return None
    return bundle["engine"]


def get_engine() -> Recommender:
    """The shared Recommender (loaded once per process)."""
    global _engine
    if _engine is None:
        _engine = _load_bundle(MODEL_PATH) if MODEL_PATH.exists() else None
        if _engine is None:
            from reco.train import train_serving_engine

            log.info("No usable saved models — training now (run `python -m reco.train` to do this ahead).")
            _engine = train_serving_engine(_db_path)
            save_engine(_engine)
    return _engine


def use_engine(engine: Recommender | None, db_path: str | Path | None = None) -> None:
    """Swap in a specific engine / database (used by tests and scripts). None = reload on next call."""
    global _engine, _db_path
    _engine = engine
    _db_path = Path(db_path) if db_path else None


def _context_dict(user_id: str, context: UserContext | dict | None) -> dict:
    if context is None:
        return {}
    if isinstance(context, UserContext):
        return context.model_dump(exclude_none=True)
    return dict(context)


# ---------------------------------------------------------------------------
# Contract functions
# ---------------------------------------------------------------------------


def get_recommendations(user_id: str, k: int = 10, context: UserContext | dict | None = None) -> list[RecItem]:
    """Personal top-k for a user. context: UserContext or dict with optional
    `is_gift_mode` (ignore personal history), `budget_band` ('low'|'mid'|'high' = hard price filter),
    `top_categories` (gift mode: categories the recipient likes)."""
    history = load_user_history(user_id, _db_path)
    return get_engine().recommend(history, k=k, context=_context_dict(user_id, context))


def get_similar(product_id: str, k: int = 10) -> list[RecItem]:
    """Alternatives to a product (works for brand-new products via their text)."""
    return get_engine().similar(product_id, k)


def get_complements(product_id: str, k: int = 10) -> list[RecItem]:
    """Products that go with this one (phone -> cover, charger)."""
    return get_engine().complements(product_id, k)


def get_trending(k: int = 10, category_id: str | None = None) -> list[RecItem]:
    """Trending right now; category_id may be a parent category (C001 = all Electronics)."""
    return get_engine().trending(k, category_id)


def rank_offers_for_user(user_id: str, offers: list[Offer]) -> list[RankedOffer]:
    """Live offers ordered by relevance for this user, each with a reason."""
    return rank_offers(get_engine(), load_user_history(user_id, _db_path), offers)


def build_user_context(user_id: str) -> UserContext:
    """Budget band + top categories (no raw personal data) for offers and LLM wording."""
    return _build_context(get_engine(), load_user_history(user_id, _db_path))
