"""The API's last step before showing recommendations: apply what the USER asked for.

    reco (Phase 2) candidates  ->  post-purchase switch  ->  user controls  ->  explore mix  ->  top-up

- Post-purchase: just bought a phone? Hide other phones (substitutes), show covers (complements).
- User controls: budget, "not interested" (product / brand / category), gift mode, reset.
- Explore slider: swap some familiar picks for popular items from categories the user never browses.

The real reco module may already do some of this; running it again here is harmless
(filters are idempotent) and keeps the demo working even with the stubs.
Results are cached for 10 minutes; any new event or control change clears that user's cache.
"""

from __future__ import annotations

import time

from common.schemas import RecItem, UserContext

from api import store
from api.modules import call
from api.prefs import UserPrefs, get_prefs

CACHE_TTL_SECONDS = 600
_cache: dict[tuple, tuple[float, list[dict]]] = {}


# ---------------------------------------------------------------------------
# Cache (simple dict: key -> (time stored, value))
# ---------------------------------------------------------------------------


def cache_get(key: tuple) -> list[dict] | None:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    return None


def cache_set(key: tuple, value: list[dict]) -> None:
    _cache[key] = (time.time(), value)


def invalidate_user(user_id: str) -> None:
    """Forget cached recs of one user (call after they click, buy or change a control)."""
    for key in [k for k in _cache if k[1] == user_id]:
        del _cache[key]


def clear_cache() -> None:
    _cache.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def budget_band(budget_max: float | None) -> str:
    """Map a ₹ budget onto the bands reco understands."""
    if budget_max is None:
        return "high"
    return "low" if budget_max <= 1000 else "mid" if budget_max <= 5000 else "high"


def enrich(recs: list[RecItem], k: int | None = None) -> list[dict]:
    """RecItem -> dict with the product fields a card needs (title, price, …). Drops unknown IDs."""
    products = store.get_products([r.product_id for r in recs])
    out = [{**r.model_dump(), **products[r.product_id]} for r in recs if r.product_id in products]
    return out[:k] if k else out


def _allowed(product: dict, prefs: UserPrefs, budget_max: float | None, minor: bool) -> bool:
    """Every rule a single product must pass to be shown."""
    if product["product_id"] in prefs.hidden_products:
        return False
    if product["brand"] in prefs.hidden_brands or product["category_id"] in prefs.hidden_categories:
        return False
    if budget_max is not None and float(product["price"]) > budget_max:
        return False
    if minor and int(product.get("age_restricted") or 0) == 1:
        return False
    return int(product.get("stock_qty") or 0) > 0


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def recommend(user_id: str, k: int = 10, budget_max: float | None = None,
              gift_mode: bool | None = None, explore: float | None = None) -> list[dict]:
    """Personalized, explained, user-controlled recommendations (list of card dicts)."""
    prefs = get_prefs(user_id)
    # Query parameters win over saved controls (handy for testing in /docs).
    budget_max = prefs.budget_max if budget_max is None else budget_max
    gift_mode = prefs.gift_mode if gift_mode is None else gift_mode
    explore = prefs.explore if explore is None else explore

    key = ("recommend", user_id, k, budget_max, gift_mode, explore, prefs.model_dump_json())
    cached = cache_get(key)
    if cached is not None:
        return cached

    minor = store.is_minor(user_id)
    picked: list[RecItem] = []
    context_categories: list[str] = []

    if prefs.reset_at and not store.user_events(user_id, since=prefs.reset_at):
        # Fresh start after "Reset my profile": nothing personal until they click again.
        candidates = [r.model_copy(update={"reason": "Fresh start — popular with everyone right now"})
                      for r in call("get_trending", k=k * 4)]
        recent: list[dict] = []
    else:
        context = call("build_user_context", user_id)
        if not isinstance(context, UserContext):
            context = UserContext(user_id=user_id, budget_band="mid")
        context_categories = list(context.top_categories)
        context =context.model_copy(update={"is_gift_mode": gift_mode, "budget_band": budget_band(budget_max)})
        candidates = list(call("get_recommendations", user_id, k=k * 4, context=context))
        # A gift is for someone else, so "you just bought X" shouldn't steer it.
        recent = [] if gift_mode else store.recent_purchases(user_id)

    # --- Post-purchase switch: complements first, no substitutes of what they just bought ---
    bought_ids = {p["product_id"] for p in recent if not int(p["is_consumable"] or 0)}
    bought_cats = {p["category_id"] for p in recent if not int(p["is_consumable"] or 0)}
    for purchase in recent[:3]:
        if int(purchase["is_consumable"] or 0):
            continue  # consumables are handled by "Buy again"
        for comp in call("get_complements", purchase["product_id"], k=3):
            picked.append(comp.model_copy(update={
                "reason": f"Goes with the {purchase['title']} you just bought", "source": "post_purchase"}))

    # --- Candidates from reco, minus substitutes of recent purchases ---
    products = store.get_products([r.product_id for r in candidates + picked])
    personal = [r for r in candidates
                if r.product_id in products
                and r.product_id not in bought_ids
                and products[r.product_id]["category_id"] not in bought_cats]

    # --- Explore slider: replace up to half the slots with something new ---
    n_explore = round(explore * k / 2)
    explore_recs: list[RecItem] = []
    if n_explore:
        # "Familiar" = the categories the user actually likes. The real reco returns deliberately
        # diverse k*4 lists, so taking every category in `personal` would leave nothing to explore.
        familiar = set(context_categories) or {products[r.product_id]["category_id"]
                                               for r in personal[:k] if r.product_id in products}
        trending = call("get_trending", k=k * 4)
        products.update(store.get_products([r.product_id for r in trending]))
        for r in trending:
            p = products.get(r.product_id)
            if p and p["category_id"] not in familiar and p["category_id"] not in bought_cats:
                explore_recs.append(r.model_copy(update={
                    "reason": f"Something new to explore: popular in {p['category_name']}", "source": "explore"}))

    final: list[RecItem] = []
    seen: set[str] = set()

    def add(recs: list[RecItem], limit: int) -> None:
        for r in recs:
            if len(final) >= limit:
                return
            p = products.get(r.product_id)
            if p and r.product_id not in seen and _allowed(p, prefs, budget_max, minor):
                final.append(r)
                seen.add(r.product_id)

    add(picked, k)
    add(personal, k - n_explore)
    add(explore_recs, k)
    if len(final) < k:  # not enough left after filtering -> fill with popular items
        trending = call("get_trending", k=k * 5)
        products.update(store.get_products([r.product_id for r in trending]))
        add([r.model_copy(update={"reason": "Popular right now"}) for r in trending
             if products.get(r.product_id, {}).get("category_id") not in bought_cats], k)

    result = enrich(final, k)
    cache_set(key, result)
    return result


def similar(product_id: str, k: int = 10) -> list[dict]:
    key = ("similar", "*", product_id, k)
    cached = cache_get(key)
    if cached is None:
        cached = enrich(call("get_similar", product_id, k=k), k)
        cache_set(key, cached)
    return cached


def complements(product_id: str, k: int = 10) -> list[dict]:
    key = ("complements", "*", product_id, k)
    cached = cache_get(key)
    if cached is None:
        cached = enrich(call("get_complements", product_id, k=k), k)
        cache_set(key, cached)
    return cached


def trending(k: int = 10, category_id: str | None = None) -> list[dict]:
    key = ("trending", "*", k, category_id)
    cached = cache_get(key)
    if cached is None:
        cached = enrich(call("get_trending", k=k, category_id=category_id), k)
        cache_set(key, cached)
    return cached
