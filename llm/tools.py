"""The ONLY tools the chat agent may use (an allowlist), mapped to reco / offers functions.

What is "tool use"? Claude can't look things up by itself. We describe some tools (name +
JSON schema); Claude replies "please run search_products with {query: 'earbuds'}"; OUR code
runs it and sends the result back; Claude then answers using real data.

Safety built in here:
- Tool inputs are validated with pydantic — bad input becomes an error message to Claude,
  never a crash or a weird DB query.
- No tool takes a user_id: every call is bound to the logged-in user by ToolContext, so
  Claude cannot read another user's data even if asked to.
- There is no payment / order / refund tool. add_to_cart only prepares a confirmation button.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from common.schemas import UserContext
from llm import catalog

# Real modules when they exist, stubs until then (CLAUDE.md rule 4).
try:
    from reco import build_user_context, get_complements, get_recommendations, get_similar
except ImportError:
    from common.stubs import build_user_context, get_complements, get_recommendations, get_similar
try:
    from offers import fetch_live_offers
except ImportError:
    from common.stubs import fetch_live_offers

from llm.templates import discount_text

_PID = r"^P\d{5}$"


# ---------------------------------------------------------------------------
# Tool inputs (pydantic models = the JSON schema Claude sees + validation)
# ---------------------------------------------------------------------------


class ToolInput(BaseModel):
    # Unknown fields are an error, so Claude can't sneak in e.g. user_id="U00002".
    model_config = ConfigDict(extra="forbid")


class SearchProductsInput(ToolInput):
    """Search the catalog by keywords, with optional price range and category."""

    query: str = Field(min_length=1, max_length=100, description="What the user wants, e.g. 'wireless earbuds'")
    max_price: float | None = Field(default=None, gt=0, description="Highest price in rupees")
    min_price: float | None = Field(default=None, ge=0, description="Lowest price in rupees")
    category: str | None = Field(default=None, max_length=40, description="Category name or ID, e.g. 'Footwear'")
    k: int = Field(default=5, ge=1, le=10, description="How many results")


class RecommendationsInput(ToolInput):
    """Personal recommendations for the current user."""

    k: int = Field(default=5, ge=1, le=10)
    budget_band: str | None = Field(default=None, pattern="^(low|mid|high)$", description="low <₹1000, mid <₹5000, high")
    gift_mode: bool = Field(default=False, description="True if shopping for someone else")


class ProductInput(ToolInput):
    product_id: str = Field(pattern=_PID, description="Product ID like P00007")
    k: int = Field(default=5, ge=1, le=10)


class CompareInput(ToolInput):
    product_ids: list[str] = Field(min_length=2, max_length=4, description="2–4 product IDs")

    @field_validator("product_ids")
    @classmethod
    def _ids(cls, v: list[str]) -> list[str]:
        bad = [p for p in v if not re.match(_PID, p)]
        if bad:
            raise ValueError(f"not product IDs: {bad}")
        return v


class ExplainInput(ToolInput):
    product_id: str = Field(pattern=_PID)


class NoInput(ToolInput):
    pass


class AddToCartInput(ToolInput):
    product_id: str = Field(pattern=_PID)
    quantity: int = Field(default=1, ge=1, le=5)


# ---------------------------------------------------------------------------
# Per-turn state: who the user is + what the tools showed (used for grounding later)
# ---------------------------------------------------------------------------


@dataclass
class ToolContext:
    user_id: str
    seen_products: list[str] = field(default_factory=list)   # every product a tool returned
    last_results: list[str] = field(default_factory=list)    # products from the latest product tool
    offer_codes: set[str] = field(default_factory=set)       # codes get_my_offers returned
    buttons: list[str] = field(default_factory=list)         # confirmation buttons (add_to_cart)
    tool_calls: list[str] = field(default_factory=list)

    def remember(self, product_ids: list[str]) -> None:
        self.last_results = list(product_ids)
        self.seen_products += [p for p in product_ids if p not in self.seen_products]


def _facts(product_id: str, reason: str = "") -> dict:
    """What a tool tells Claude about a product — straight from the DB."""
    p = catalog.get_product(product_id)
    out = {
        "product_id": product_id, "title": p["title"], "brand": p["brand"],
        "category": catalog.category_name(p["category_id"]), "price_inr": float(p["price"]),
        "mrp_inr": float(p["mrp"]), "avg_rating": float(p["avg_rating"]), "rating_count": int(p["rating_count"] or 0),
        "in_stock": float(p["stock_qty"] or 0) > 0,
    }
    if reason:
        out["why"] = reason
    return out


def _allowed_for_user(ctx: ToolContext, product_id: str) -> bool:
    return catalog.product_exists(product_id) and not (catalog.is_minor(ctx.user_id) and catalog.is_age_restricted(product_id))


def _recs_result(ctx: ToolContext, recs) -> dict:
    items = [_facts(r.product_id, r.reason) for r in recs if _allowed_for_user(ctx, r.product_id)]
    ctx.remember([i["product_id"] for i in items])
    return {"products": items} if items else {"products": [], "note": "nothing found"}


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

# Words users type -> words our catalog uses (English + Hinglish).
SYNONYMS = {
    "earphone": "earbuds", "earphones": "earbuds", "headphone": "earbuds", "headphones": "earbuds",
    "earbud": "earbuds", "tws": "earbuds", "neckband": "earbuds", "mobile": "smartphone", "phone": "smartphone",
    "phones": "smartphone", "mobiles": "smartphone", "shoes": "shoes", "shoe": "shoes", "joote": "shoes",
    "joota": "shoes", "sneaker": "sneakers", "chai": "tea", "kitab": "paperback", "book": "paperback",
    "books": "paperback", "tshirt": "t-shirt", "kapde": "clothing", "charger": "charger", "cover": "cover",
}
_STOP = {"the", "a", "an", "for", "me", "my", "i", "want", "need", "show", "find", "some", "any", "good", "best",
         "under", "below", "with", "and", "or", "of", "to", "in", "on", "please", "buy", "cheap", "price",
         "rs", "inr", "mujhe", "chahiye", "dikhao", "ke", "ka", "ki", "andar", "neeche", "tak", "se", "kam", "wala",
         "wale", "wali", "acha", "accha", "kuch", "koi", "hai", "is", "are", "what", "which", "products", "product"}


def _keywords(query: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9\-]+", query.lower()) if w not in _STOP and not w.isdigit()]
    return list(dict.fromkeys(SYNONYMS.get(w, w) for w in words))


def search_products(ctx: ToolContext, inp: SearchProductsInput) -> dict:
    """Keyword search over title / tags / category / description, best matches first."""
    df = catalog.products()
    df = df[df["stock_qty"].astype(float) > 0]
    if catalog.is_minor(ctx.user_id):
        df = df[df["age_restricted"].astype(int) == 0]
    if inp.max_price is not None:
        df = df[df["price"].astype(float) <= inp.max_price]
    if inp.min_price is not None:
        df = df[df["price"].astype(float) >= inp.min_price]
    if inp.category:
        cat_ids = [inp.category] if re.match(r"^C\d{3}$", inp.category) else catalog.category_ids_matching(inp.category)
        cat_ids += [c for cid in cat_ids for c in catalog.child_categories(cid)]
        if cat_ids:
            df = df[df["category_id"].isin(cat_ids)]

    words = _keywords(inp.query)
    scored = []
    for r in df.to_dict("records"):
        title, tags = str(r["title"]).lower(), str(r["tags"]).lower()
        cat, desc = catalog.category_name(r["category_id"]).lower(), str(r["description"]).lower()
        score = sum(2 * (w in title) + 1.5 * (w in tags) + 1 * (w in cat) + 0.5 * (w in desc) for w in words)
        if score > 0 or not words:
            scored.append((score, float(r["avg_rating"]), r["product_id"]))
    scored.sort(reverse=True)
    ids = [pid for _, _, pid in scored[: inp.k]]
    ctx.remember(ids)
    if not ids:
        return {"products": [], "note": "no catalog products match; tell the user honestly"}
    return {"products": [_facts(pid) for pid in ids]}


def tool_get_recommendations(ctx: ToolContext, inp: RecommendationsInput) -> dict:
    user_ctx = build_user_context(ctx.user_id)
    context = UserContext(user_id=ctx.user_id, budget_band=inp.budget_band or user_ctx.budget_band,
                          top_categories=user_ctx.top_categories, is_gift_mode=inp.gift_mode)
    return _recs_result(ctx, get_recommendations(ctx.user_id, k=inp.k, context=context))


def tool_get_similar(ctx: ToolContext, inp: ProductInput) -> dict:
    if not catalog.product_exists(inp.product_id):
        raise ValueError(f"{inp.product_id} is not in the catalog")
    return _recs_result(ctx, get_similar(inp.product_id, k=inp.k))


def tool_get_complements(ctx: ToolContext, inp: ProductInput) -> dict:
    if not catalog.product_exists(inp.product_id):
        raise ValueError(f"{inp.product_id} is not in the catalog")
    return _recs_result(ctx, get_complements(inp.product_id, k=inp.k))


def compare_products(ctx: ToolContext, inp: CompareInput) -> dict:
    missing = [p for p in inp.product_ids if not _allowed_for_user(ctx, p)]
    if missing:
        raise ValueError(f"not in the catalog: {missing}")
    ctx.remember(inp.product_ids)
    return {"products": [_facts(p) for p in inp.product_ids]}


def explain_recommendation(ctx: ToolContext, inp: ExplainInput) -> dict:
    """Why might this product suit the user? Uses the rec engine's own reason when it has one."""
    if not _allowed_for_user(ctx, inp.product_id):
        raise ValueError(f"{inp.product_id} is not in the catalog")
    ctx.remember([inp.product_id])
    rec = next((r for r in get_recommendations(ctx.user_id, k=20) if r.product_id == inp.product_id), None)
    if rec:
        reason = rec.reason
    else:
        product = catalog.get_product(inp.product_id)
        liked = build_user_context(ctx.user_id).top_categories
        reason = (f"It is in {catalog.category_name(product['category_id'])}, a category you browse often"
                  if product["category_id"] in liked else "It is popular and well rated in its category")
    return {"product": _facts(inp.product_id), "reason": reason}


def get_my_offers(ctx: ToolContext, inp: NoInput) -> dict:
    """Live offers this user can use (codes and values from the Offers API / DB)."""
    offers = [o for o in fetch_live_offers(ctx.user_id) if not o.requires_approval][:6]
    ctx.offer_codes |= {o.code for o in offers}
    return {"offers": [{
        "code": o.code, "gives": discount_text(o), "min_cart_inr": o.min_cart_value,
        "applies_to": (catalog.get_product(o.product_id)["title"] if o.product_id and catalog.get_product(o.product_id)
                       else catalog.category_name(o.category_id) or "whole store"),
        "valid_till": o.expires_at.strftime("%d %b %Y"),
    } for o in offers]}


def add_to_cart(ctx: ToolContext, inp: AddToCartInput) -> dict:
    """Never adds directly: returns a confirmation button the user must tap."""
    if not _allowed_for_user(ctx, inp.product_id):
        raise ValueError(f"{inp.product_id} is not available")
    if float(catalog.get_product(inp.product_id)["stock_qty"] or 0) <= 0:
        raise ValueError(f"{inp.product_id} is out of stock")
    ctx.remember([inp.product_id])
    button = f"confirm_add_to_cart:{inp.product_id}:{inp.quantity}"
    if button not in ctx.buttons:
        ctx.buttons += [button, "cancel"] if "cancel" not in ctx.buttons else [button]
    return {"status": "needs_user_confirmation", "product": _facts(inp.product_id), "quantity": inp.quantity,
            "note": "A confirm button was shown. Tell the user to tap it. Nothing was added yet."}


# ---------------------------------------------------------------------------
# Registry: name -> (input model, function, description). This IS the allowlist.
# ---------------------------------------------------------------------------

TOOLS: dict[str, tuple[type[BaseModel], Callable, str]] = {
    "search_products": (SearchProductsInput, search_products,
                         "Search the store catalog by keywords with optional price range (rupees) and category."),
    "get_recommendations": (RecommendationsInput, tool_get_recommendations,
                            "Personalised product picks for the current user (optionally budget or gift mode)."),
    "get_similar": (ProductInput, tool_get_similar, "Products similar to (alternatives for) a given product."),
    "get_complements": (ProductInput, tool_get_complements,
                        "Products that go well WITH a given product (accessories, add-ons)."),
    "compare_products": (CompareInput, compare_products, "Side-by-side facts for 2–4 products."),
    "explain_recommendation": (ExplainInput, explain_recommendation, "Why a product suits the current user."),
    "get_my_offers": (NoInput, get_my_offers, "The current user's live offers and coupon codes."),
    "add_to_cart": (AddToCartInput, add_to_cart,
                    "Prepare adding a product to the cart. Shows a confirm button; does NOT add it."),
}


def _schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    schema.pop("title", None)
    schema.pop("description", None)
    schema.setdefault("properties", {})
    return schema


TOOL_SCHEMAS: list[dict] = [
    {"name": name, "description": desc, "input_schema": _schema(model)} for name, (model, _, desc) in TOOLS.items()
]


def run_tool(ctx: ToolContext, name: str, raw_input: dict) -> tuple[dict, bool]:
    """Validate + run one tool. Returns (result, is_error). Never raises."""
    ctx.tool_calls.append(name)
    if name not in TOOLS:
        return {"error": f"unknown tool '{name}'"}, True
    model, fn, _ = TOOLS[name]
    try:
        inp = model.model_validate(raw_input or {})
    except ValidationError as exc:
        return {"error": "invalid input", "details": [e["msg"] for e in exc.errors()][:3]}, True
    try:
        return fn(ctx, inp), False
    except ValueError as exc:
        return {"error": str(exc)}, True
    except Exception:  # a bug in reco/offers must not crash the chat
        return {"error": "tool failed, try something else"}, True
