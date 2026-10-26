"""Fake-but-valid versions of EVERY function in the Module Contracts (CLAUDE.md).

Why: Phases 1–6 run in parallel. If your module needs another one that isn't built
yet, import the stub instead and swap later:

    try:
        from reco import get_recommendations      # real module (when it exists)
    except ImportError:
        from common.stubs import get_recommendations

Stubs use the sample data (or whatever is loaded in app.db), so results look realistic:
real product IDs, real offers, sensible reasons. They have NO real intelligence and
NEVER call outside services (no Claude, no Telegram, no network).

The HTTP endpoints of `offers` (Offers API) and `api` (Main API) are thin wrappers over
these Python functions, so stubs are provided for the functions behind them.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from functools import lru_cache

import pandas as pd

from common.config import settings
from common.db import data_folder, read_table, table_counts
from common.logging_utils import get_logger, now_local
from common.schemas import (
    EVENT_WEIGHTS,
    ChatReply,
    GuardrailResult,
    Offer,
    RankedOffer,
    RecItem,
    TriggerType,
    UserContext,
)

log = get_logger("stubs")

# ---------------------------------------------------------------------------
# Data access (cached: stubs read the data once per process)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=None)
def _table(name: str) -> pd.DataFrame:
    """Read a table from app.db; if the DB is missing/empty, read data/sample/<name>.csv instead."""
    try:
        if table_counts().get(name, -1) > 0:
            return read_table(name)
    except Exception:  # no DB yet -> fall back to CSV
        pass
    path = data_folder("sample") / f"{name}.csv"
    return pd.read_csv(path, keep_default_na=False) if path.exists() else pd.DataFrame()


def _products() -> pd.DataFrame:
    return _table("products")


def _user_events(user_id: str) -> pd.DataFrame:
    events = _table("events")
    return events[events["user_id"] == user_id] if not events.empty else events


def _is_minor(user_id: str) -> bool:
    users = _table("users")
    row = users[users["user_id"] == user_id]
    return bool(len(row)) and int(row.iloc[0]["is_minor"] or 0) == 1


def _popularity() -> pd.Series:
    """Weighted event score per product (view 1 ... purchase 6)."""
    events = _table("events")
    weights = events["event_type"].map(EVENT_WEIGHTS).fillna(0)
    return weights.groupby(events["product_id"]).sum().sort_values(ascending=False)


def _safe_products(user_id: str | None = None) -> pd.DataFrame:
    """In-stock products, minus age-restricted ones for minors."""
    p = _products()
    p = p[p["stock_qty"].astype(float) > 0]
    if user_id and _is_minor(user_id):
        p = p[p["age_restricted"].astype(int) == 0]
    return p


def _to_recs(df: pd.DataFrame, scores: pd.Series, reason: str, source: str, k: int) -> list[RecItem]:
    # (column must not start with "_": itertuples() would rename it)
    df = df.assign(rec_score=df["product_id"].map(scores).fillna(0.0)).sort_values("rec_score", ascending=False)
    top = df.head(k)
    max_score = float(top["rec_score"].max() or 1.0) or 1.0
    return [
        RecItem(product_id=r.product_id, score=round(float(r.rec_score) / max_score, 4),
                reason=reason.format(title=r.title, brand=r.brand), source=source)
        for r in top.itertuples()
    ]


# ---------------------------------------------------------------------------
# reco (Phase 2)
# ---------------------------------------------------------------------------


def build_user_context(user_id: str) -> UserContext:
    """Top categories from the user's weighted events; budget band from prices they looked at."""
    ev = _user_events(user_id)
    products = _products().set_index("product_id")
    ev = ev[ev["product_id"].isin(products.index)]
    if ev.empty:
        return UserContext(user_id=user_id, budget_band="mid", top_categories=[])
    cats = products.loc[ev["product_id"], "category_id"].values
    weights = ev["event_type"].map(EVENT_WEIGHTS).fillna(0).values
    top = pd.Series(weights, index=cats).groupby(level=0).sum().sort_values(ascending=False)
    median_price = float(products.loc[ev["product_id"], "price"].astype(float).median())
    band = "low" if median_price < 500 else "mid" if median_price < 3000 else "high"
    return UserContext(user_id=user_id, budget_band=band, top_categories=list(top.index[:3]))


def get_trending(k: int = 10, category_id: str | None = None) -> list[RecItem]:
    """Most-interacted products in the last 14 days of data (optionally one category or its children)."""
    events = _table("events")
    recent = events[events["timestamp"] >= (pd.to_datetime(events["timestamp"]).max()
                                            - pd.Timedelta(days=14)).isoformat()]
    scores = recent["event_type"].map(EVENT_WEIGHTS).fillna(0).groupby(recent["product_id"]).sum()
    p = _safe_products()
    if category_id:
        cats = _table("categories")
        children = cats[cats["parent_id"] == category_id]["category_id"].tolist()
        p = p[p["category_id"].isin([category_id, *children])]
    return _to_recs(p, scores, "Trending this week: {title}", "popularity", k)


def get_recommendations(user_id: str, k: int = 10, context: UserContext | dict | None = None) -> list[RecItem]:
    """Popular products from the user's top categories, skipping things they already bought.

    context (optional): UserContext or dict. Honours `is_gift_mode` (-> trending instead of
    personal taste) and `budget_band` (low/mid/high price filter).
    """
    ctx = context
    if isinstance(context, dict):  # allow plain dicts like {"is_gift_mode": True}
        ctx = UserContext(**{"user_id": user_id, "budget_band": "mid", **context})
    user_ctx = build_user_context(user_id)
    if not user_ctx.top_categories or (ctx and ctx.is_gift_mode):
        recs = get_trending(k * 2)
        reason = "Popular gift pick" if ctx and ctx.is_gift_mode else "Popular with shoppers like you"
        recs = [r.model_copy(update={"reason": reason}) for r in recs]
        return _filter_minor(user_id, recs)[:k]

    p = _safe_products(user_id)
    bought = set(_user_events(user_id).query("event_type == 'purchase'")["product_id"])
    p = p[~p["product_id"].isin(bought)]
    if ctx and ctx.budget_band:
        limits = {"low": (0, 1000), "mid": (0, 5000), "high": (0, float("inf"))}
        lo, hi = limits.get(ctx.budget_band, (0, float("inf")))
        p = p[p["price"].astype(float).between(lo, hi)]
    cats = _table("categories").set_index("category_id")["name"]
    in_taste = p[p["category_id"].isin(user_ctx.top_categories)]
    recs: list[RecItem] = []
    for cid in user_ctx.top_categories:
        subset = in_taste[in_taste["category_id"] == cid]
        recs += _to_recs(subset, _popularity(), f"Because you browse {cats.get(cid, cid)}", "stub_popularity", k)
    recs.sort(key=lambda r: r.score, reverse=True)
    if len(recs) < k:  # top up with general popular items
        have = {r.product_id for r in recs}
        recs += [r for r in _to_recs(p, _popularity(), "Popular right now", "stub_popularity", k * 2)
                 if r.product_id not in have]
    return recs[:k]


def _filter_minor(user_id: str, recs: list[RecItem]) -> list[RecItem]:
    allowed = set(_safe_products(user_id)["product_id"])
    return [r for r in recs if r.product_id in allowed]


def get_similar(product_id: str, k: int = 10) -> list[RecItem]:
    """Same category, closest price (these are substitutes)."""
    p = _safe_products()
    base = _products()[_products()["product_id"] == product_id]
    if base.empty:
        return []
    price = float(base.iloc[0]["price"])
    same = p[(p["category_id"] == base.iloc[0]["category_id"]) & (p["product_id"] != product_id)]
    closeness = 1 / (1 + (same["price"].astype(float) - price).abs() / price)
    return _to_recs(same, pd.Series(closeness.values, index=same["product_id"]),
                    "Similar to what you viewed: {title}", "stub_content", k)


# Category-level complement rules (stub only; Phase 2 learns these from data).
_COMPLEMENT_CATS = {"C009": ["C010"], "C019": ["C020"], "C012": ["C020"], "C013": ["C017"],
                    "C011": ["C012"], "C015": ["C016"], "C023": ["C024"]}


def get_complements(product_id: str, k: int = 10) -> list[RecItem]:
    """Products that go WITH this one (phone -> cover/charger), most popular first."""
    base = _products()[_products()["product_id"] == product_id]
    if base.empty:
        return []
    targets = _COMPLEMENT_CATS.get(base.iloc[0]["category_id"], [])
    p = _safe_products()
    p = p[p["category_id"].isin(targets)]
    return _to_recs(p, _popularity(), "Goes well with " + str(base.iloc[0]["title"]), "stub_complement", k)


def rank_offers_for_user(user_id: str, offers: list[Offer]) -> list[RankedOffer]:
    """Score = category match with the user's taste + a little for offer value."""
    ctx = build_user_context(user_id)
    ranked = []
    for o in offers:
        match = o.category_id in ctx.top_categories if o.category_id else False
        score = (0.6 if match else 0.2) + min(o.value, 40) / 100
        reason = "Matches categories you like" if match else "General offer"
        ranked.append(RankedOffer(offer_id=o.offer_id, score=round(score, 3), reason=reason))
    return sorted(ranked, key=lambda r: r.score, reverse=True)


# ---------------------------------------------------------------------------
# offers (Phase 3)
# ---------------------------------------------------------------------------


def _row_to_offer(row: pd.Series) -> Offer:
    data = {k: (None if v in ("", None) or (isinstance(v, float) and pd.isna(v)) else v) for k, v in row.items()}
    return Offer(**data)


def _all_offers() -> list[Offer]:
    return [_row_to_offer(r) for _, r in _table("offers").iterrows()]


def fetch_live_offers(user_id: str | None = None) -> list[Offer]:
    """Active offers valid right now (what GET /offers/live returns)."""
    now = now_local()
    return [o for o in _all_offers() if o.is_active and o.start_at <= now <= o.expires_at]


def generate_offers(trigger_type: str | None = None) -> list[Offer]:
    """Stub for POST /offers/generate: returns existing live offers instead of creating new ones."""
    offers = fetch_live_offers()
    return [o for o in offers if trigger_type is None or o.trigger_type == trigger_type]


def check_eligibility(user_id: str, trigger_type: str) -> GuardrailResult:
    """Consent + minor check only (real version adds frequency caps, quiet hours, segments)."""
    if _is_minor(user_id):
        return GuardrailResult(allowed=False, reason="user is a minor")
    consent = _table("user_consent")
    row = consent[consent["user_id"] == user_id]
    if row.empty or int(row.iloc[0]["opted_in"] or 0) != 1:
        return GuardrailResult(allowed=False, reason="user has not opted in")
    return GuardrailResult(allowed=True, reason=f"eligible for {trigger_type}")


def apply_offer_guardrails(user_id: str, ranked_offers: list[RankedOffer]) -> list[RankedOffer]:
    """Drop offers that break basic rules; keep the best MAX_OFFERS_PER_DAY."""
    offers = {o.offer_id: o for o in _all_offers()}
    kept = []
    for r in ranked_offers:
        o = offers.get(r.offer_id)
        if o is None or o.requires_approval:
            continue
        if o.offer_type == "pct_off" and o.value > settings.MAX_DISCOUNT_PCT:
            continue
        kept.append(r)
    return kept[: settings.MAX_OFFERS_PER_DAY]


def revalidate(offer_id: str, user_id: str) -> GuardrailResult:
    """Last check right before sending: offer still exists, active and not expired."""
    offer = next((o for o in _all_offers() if o.offer_id == offer_id), None)
    if offer is None:
        return GuardrailResult(allowed=False, reason="offer not found")
    if not offer.is_active or offer.expires_at < now_local():
        return GuardrailResult(allowed=False, reason="offer expired or inactive")
    return GuardrailResult(allowed=True, reason="ok")


# ---------------------------------------------------------------------------
# llm (Phase 4) — template text only, never calls Claude
# ---------------------------------------------------------------------------

_BLOCKED_PHRASES = ("ignore previous instructions", "ignore all instructions", "system prompt", "api key")


def write_offer_message(offer: Offer, user_context: UserContext) -> str:
    """Plain template message (this is also what the real module falls back to)."""
    what = {"pct_off": f"{offer.value:g}% off", "flat_off": f"₹{offer.value:g} off",
            "free_delivery": "free delivery", "bundle": f"{offer.value:g}% off the bundle",
            "buy_again": f"{offer.value:g}% off when you buy again", "points": f"{offer.value:g} bonus points"}
    return (f"Hi! Here's {what.get(offer.offer_type, 'a special offer')} for you. "
            f"Use code {offer.code} before {offer.expires_at:%d %b %Y}.")


def check_input(text: str) -> GuardrailResult:
    """Very small input filter: empty, too long, or obvious prompt-injection phrases."""
    if not text or not text.strip():
        return GuardrailResult(allowed=False, reason="empty message")
    if len(text) > 2000:
        return GuardrailResult(allowed=False, reason="message too long")
    if any(p in text.lower() for p in _BLOCKED_PHRASES):
        return GuardrailResult(allowed=False, reason="possible prompt injection")
    return GuardrailResult(allowed=True, reason="ok")


def validate_output(reply: ChatReply) -> GuardrailResult:
    """Grounding check: every product_id in the reply must exist in the catalog."""
    known = set(_products()["product_id"])
    unknown = [pid for pid in reply.product_ids if pid not in known]
    if unknown:
        return GuardrailResult(allowed=False, reason=f"unknown product ids: {unknown}")
    return GuardrailResult(allowed=True, reason="ok")


def chat(user_id: str, message: str) -> ChatReply:
    """Answers every message with 3 recommendations."""
    check = check_input(message)
    if not check.allowed:
        return ChatReply(text="Sorry, I can't help with that. Ask me about products!", product_ids=[], buttons=[])
    recs = get_recommendations(user_id, k=3)
    titles = _products().set_index("product_id")["title"]
    lines = [f"• {titles[r.product_id]} — {r.reason}" for r in recs]
    return ChatReply(text="Here are some picks for you:\n" + "\n".join(lines),
                     product_ids=[r.product_id for r in recs], buttons=["Show more", "Not interested"])


# ---------------------------------------------------------------------------
# bot (Phase 5) — logs instead of sending
# ---------------------------------------------------------------------------


def create_link_token(user_id: str) -> str:
    """Return a Telegram deep link like https://t.me/<bot>?start=<token> (token is not saved)."""
    username = settings.TELEGRAM_BOT_USERNAME or "your_bot"
    return f"https://t.me/{username}?start={secrets.token_urlsafe(12)}"


def send_offer(user_id: str, message: str, offer_id: str) -> bool:
    log.info("[stub] would send offer %s to %s: %s", offer_id, user_id, message)
    return True


def send_text(user_id: str, text: str, buttons: list[str] | None = None) -> bool:
    log.info("[stub] would send text to %s: %s (buttons=%s)", user_id, text, buttons or [])
    return True


# ---------------------------------------------------------------------------
# pipeline (Phase 7)
# ---------------------------------------------------------------------------


def detect_triggers(now: datetime) -> list[tuple[str, str]]:
    """Find (user_id, trigger_type) pairs: cart abandons in the 3 days before `now`."""
    events = _table("events")
    since = (now - pd.Timedelta(days=3)).isoformat()
    recent = events[(events["timestamp"] >= since) & (events["timestamp"] <= now.isoformat())]
    carts = recent[recent["event_type"] == "add_to_cart"]
    bought = set(zip(recent.query("event_type == 'purchase'")["user_id"],
                     recent.query("event_type == 'purchase'")["product_id"]))
    users = sorted({u for u, p in zip(carts["user_id"], carts["product_id"]) if (u, p) not in bought})
    return [(u, TriggerType.CART_ABANDON.value) for u in users]


def run_offer_pipeline(user_id: str, trigger_type: str) -> str:
    """Runs the approved pipeline using stubs. Returns a status: 'sent' | 'blocked' | 'no_offer'."""
    if not check_eligibility(user_id, trigger_type).allowed:
        return "blocked"
    offers = [o for o in fetch_live_offers(user_id) if o.trigger_type in (None, trigger_type)]
    ranked = apply_offer_guardrails(user_id, rank_offers_for_user(user_id, offers))
    if not ranked:
        return "no_offer"
    offer = next(o for o in offers if o.offer_id == ranked[0].offer_id)
    if not revalidate(offer.offer_id, user_id).allowed:
        return "blocked"
    message = write_offer_message(offer, build_user_context(user_id))
    return "sent" if send_offer(user_id, message, offer.offer_id) else "failed"
