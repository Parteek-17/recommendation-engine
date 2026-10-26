"""Demo personas: let a classmate "play" one of our synthetic shoppers on Telegram.

Our customers are synthetic, so a real person has no shop account. When someone presses
Start without a link, the bot offers 7 shopper types. Picking one links their chat to a
synthetic user whose HISTORY fits that type (a student with an abandoned cart, a regular
grocery buyer, ...), and the normal offer pipeline runs for that user straight away.
So 7 classmates see 7 different offers, each chosen and checked by the same rules.

Each persona says which trigger it demonstrates and how to find a fitting user in the data.
Only behaviour (events, orders, sessions) and the age group are used to pick a user.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot import store
from bot.linking import create_link_token, link_chat
from common.logging_utils import audit, get_logger

log = get_logger(__name__)

CANDIDATES_TO_CHECK = 12  # users we try per persona before falling back to "anyone free"


@dataclass(frozen=True)
class Persona:
    key: str
    button: str        # short label on the Telegram button
    story: str         # what the tester is playing
    trigger_type: str  # which offer reason this persona demonstrates
    expect: str        # what a marketing person should see happen
    sql: str           # candidate users, best fit first (one column: user_id)


_NOT_MINOR = "u.is_minor = 0"

PERSONAS: list[Persona] = [
    Persona("student", "🎓 Budget student", "a college student on a tight budget who left something in the cart",
            "cart_abandon", "a bigger-than-usual discount on the exact item left in the cart",
            f"""SELECT e.user_id FROM events e JOIN users u ON u.user_id = e.user_id
                WHERE e.event_type = 'add_to_cart' AND u.age_group = '18-24' AND {_NOT_MINOR}
                GROUP BY e.user_id ORDER BY MAX(e.timestamp) DESC"""),
    Persona("grocer", "🛒 Monthly restocker", "someone who buys the same groceries and beauty items every few weeks",
            "repurchase_due", "a small 'buy again' discount on the item that is about to run out",
            f"""SELECT e.user_id FROM events e JOIN products p ON p.product_id = e.product_id
                JOIN users u ON u.user_id = e.user_id
                WHERE e.event_type = 'purchase' AND p.is_consumable = 1 AND {_NOT_MINOR}
                GROUP BY e.user_id ORDER BY COUNT(*) DESC"""),
    Persona("gadget", "📱 New gadget owner", "someone who just bought a phone or gadget",
            "post_purchase", "a bundle discount on a matching accessory — never another phone",
            f"""SELECT e.user_id FROM events e JOIN products p ON p.product_id = e.product_id
                JOIN categories c ON c.category_id = p.category_id JOIN users u ON u.user_id = e.user_id
                WHERE e.event_type = 'purchase' AND (c.parent_id = 'C001' OR c.category_id = 'C001') AND {_NOT_MINOR}
                GROUP BY e.user_id ORDER BY MAX(e.timestamp) DESC"""),
    Persona("lapsed", "😴 Lapsed customer", "a past customer who hasn't shopped for over a month",
            "win_back", "a 'we miss you' discount on their favourite category",
            f"""SELECT o.user_id FROM orders o JOIN users u ON u.user_id = o.user_id WHERE {_NOT_MINOR}
                GROUP BY o.user_id HAVING COUNT(*) >= 2 ORDER BY MAX(o.order_date) ASC"""),
    Persona("wishlist", "💖 Wishlist watcher", "someone who saves things to a wishlist and waits for a better price",
            "price_drop", "a flat ₹ discount on a wishlisted item",
            f"""SELECT e.user_id FROM events e JOIN users u ON u.user_id = e.user_id
                WHERE e.event_type = 'wishlist' AND {_NOT_MINOR}
                GROUP BY e.user_id ORDER BY COUNT(*) DESC"""),
    Persona("gifter", "🪔 Diwali gift shopper", "someone who often shops for gifts, during the Diwali sale",
            "festival", "a festival discount on the categories they shop most",
            f"""SELECT s.user_id FROM sessions s JOIN users u ON u.user_id = s.user_id
                WHERE s.is_gift = 1 AND {_NOT_MINOR} GROUP BY s.user_id ORDER BY COUNT(*) DESC"""),
    Persona("premium", "💎 Premium regular", "a high-spending regular who left something in the cart",
            "cart_abandon", "NO discount — just a reminder, because this shopper buys anyway (saves margin)",
            f"""SELECT o.user_id FROM orders o JOIN users u ON u.user_id = o.user_id
                WHERE {_NOT_MINOR} AND EXISTS (SELECT 1 FROM events e WHERE e.user_id = o.user_id
                                               AND e.event_type = 'add_to_cart')
                GROUP BY o.user_id HAVING COUNT(*) >= 4 ORDER BY AVG(o.total_amount) DESC"""),
]
BY_KEY = {p.key: p for p in PERSONAS}


def _taken() -> set[str]:
    """Users already played by someone (active link) — two classmates never share a shopper."""
    with store.db() as conn:
        rows = conn.execute("SELECT user_id FROM telegram_links WHERE active = 1 AND chat_id IS NOT NULL").fetchall()
    return {r["user_id"] for r in rows}


def _holds_live_offer(user_id: str, now) -> bool:
    """Already holding an unused, still-valid coupon? Then the no-stacking rule (rightly) blocks
    a new one, and the tester would see no offer, so such users don't make good demo shoppers."""
    with store.db() as conn:
        row = conn.execute(
            "SELECT 1 FROM offer_sends s JOIN offers o ON o.offer_id = s.offer_id "
            "WHERE s.user_id = ? AND s.status IN ('queued', 'sent', 'opened', 'clicked') "
            "AND o.is_active = 1 AND o.expires_at >= ? LIMIT 1",
            (user_id, now.isoformat(timespec="seconds"))).fetchone()
    return row is not None


def _fits(persona: Persona, user_id: str) -> bool:
    """Would the pipeline actually show this persona's behaviour for this user?"""
    from pipeline.offer_pipeline import likely_buyer  # lazy: the pipeline imports the bot package
    from common.logging_utils import now_local

    now = now_local()
    if _holds_live_offer(user_id, now):
        return False
    is_likely_buyer = likely_buyer(user_id, persona.trigger_type, now) is not None
    if persona.key == "premium":
        return is_likely_buyer
    if is_likely_buyer:
        return False  # they'd get a reminder, not this persona's offer
    if persona.trigger_type == "festival":
        return True  # store-wide festival offers exist for everyone during the sale
    from offers.generator import generate_offers

    try:
        return bool(generate_offers(persona.trigger_type, user_id, save=False))
    except Exception as exc:
        log.warning("persona check failed for %s (%s)", user_id, exc)
        return False


# Checking a user means building their offers (~2-5 s each on the full data), far too slow
# for a button tap. So `prepare_pool()` checks users ONCE at start-up (scripts/preflight.py)
# and stores fitting ones in persona_pool; a tap then just takes the first free user from it.
POOL_SIZE = 8
SCAN_LIMIT = {"premium": 250}  # premium regulars in the "high" budget band are rarer: look further

_POOL_DDL = "CREATE TABLE IF NOT EXISTS persona_pool (persona TEXT, user_id TEXT, rank INTEGER)"


def prepare_pool(size: int = POOL_SIZE) -> dict[str, int]:
    """Find up to `size` fitting users per persona and save them. Returns {persona: users found}."""
    found: dict[str, list[str]] = {}
    with store.db() as conn:
        candidates = {p.key: [r[0] for r in conn.execute(p.sql).fetchall()] for p in PERSONAS}
    for p in PERSONAS:
        fits = []
        for user_id in candidates[p.key][: SCAN_LIMIT.get(p.key, size * 3)]:
            if _fits(p, user_id):
                fits.append(user_id)
                if len(fits) >= size:
                    break
        found[p.key] = fits
    with store.db() as conn:
        conn.execute(_POOL_DDL)
        conn.execute("DELETE FROM persona_pool")
        conn.executemany("INSERT INTO persona_pool VALUES (?, ?, ?)",
                         [(key, u, i) for key, users in found.items() for i, u in enumerate(users)])
    return {key: len(users) for key, users in found.items()}


def _pool(persona_key: str) -> list[str]:
    with store.db() as conn:
        conn.execute(_POOL_DDL)
        return [r[0] for r in conn.execute("SELECT user_id FROM persona_pool WHERE persona = ? ORDER BY rank",
                                           (persona_key,))]


def pick_user(persona: Persona) -> str | None:
    """A free synthetic user whose data fits `persona`: from the prepared pool if possible,
    else a (slower) live search, else any free adult user."""
    taken = _taken()
    pooled = [u for u in _pool(persona.key) if u not in taken]
    if pooled:
        return pooled[0]
    with store.db() as conn:
        candidates = [r[0] for r in conn.execute(persona.sql).fetchall() if r[0] not in taken]
        anyone = [r[0] for r in conn.execute("SELECT user_id FROM users WHERE is_minor = 0 ORDER BY user_id")
                  if r[0] not in taken]
    for user_id in candidates[:CANDIDATES_TO_CHECK]:
        if _fits(persona, user_id):
            return user_id
    log.warning("no fitting user for persona %s; using the first free one", persona.key)
    return (candidates or anyone or [None])[0]


def assign(persona_key: str, chat_id: int | str) -> str | None:
    """Link `chat_id` to a fitting synthetic user, opt them in for the demo. Returns the user_id."""
    persona = BY_KEY[persona_key]
    user_id = pick_user(persona)
    if user_id is None:
        return None
    # Same path as a real customer: a fresh single-use token, then "pressing Start" with it.
    token = create_link_token(user_id).split("?start=")[1]
    link_chat(token, chat_id)
    # Testers try the bot at any hour; they can set real quiet hours with /settings.
    store.update_consent(user_id, quiet_start="00:00", quiet_end="00:00")
    store.save_tester(user_id, persona.key, persona.trigger_type)
    audit("bot", user_id, "assign_persona", "linked", persona.key, {"trigger_type": persona.trigger_type})
    return user_id


_STATUS_TEXT = {
    "sent": "✅ Offer sent (above)",
    "reminder_sent": "✅ Reminder sent, no discount (above)",
    "dry_run": "🧪 Dry run: this offer WOULD be sent",
    "reminder_dry_run": "🧪 Dry run: a no-discount reminder WOULD be sent",
    "holdout": "⏸ You're in the control group: no offer, so we can measure what offers add",
    "blocked": "⛔ Not allowed to message you right now",
    "no_offer": "🤷 No offer fits you right now",
    "guardrail_blocked": "⛔ Every possible offer broke a rule, so nothing was sent",
    "revalidate_failed": "⛔ The offer changed just before sending, so it was stopped",
    "offers_api_down": "⚠️ The Offers service is not running",
}


def explain_result(result) -> str:
    """'Behind the scenes' note for testers and judges: what the AI chose and what the rules checked."""
    lines = ["🔍 Behind the scenes", _STATUS_TEXT.get(result.status, f"Result: {result.status}")]
    if result.status not in ("sent", "reminder_sent") and result.reason:
        lines.append(f"Why: {result.reason}")
    who = {"ai": "🤖 Claude chose it", "ranker": "📊 Rec engine chose it", "rule": "📏 A business rule decided"}
    if result.chosen_by:
        lines.append(f"{who.get(result.chosen_by, result.chosen_by)}: {result.choice_reason}")
    if result.ai_rejected:
        lines.append(f"🛡 AI suggestion overruled by the rules: {result.ai_rejected}")
    checks = [s for s in result.steps if s in ("eligibility", "guardrails", "revalidate")]
    if checks:
        lines.append("Rules passed: " + " → ".join(checks))
    return "\n".join(lines)


def profile_summary(user_id: str) -> str:
    """A few facts from the synthetic history, so the tester knows who they are playing."""
    with store.db() as conn:
        user = conn.execute("SELECT city, age_group FROM users WHERE user_id = ?", (user_id,)).fetchone()
        orders = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(total_amount), 0) AS total FROM orders "
                              "WHERE user_id = ?", (user_id,)).fetchone()
        recent = conn.execute(
            "SELECT p.title, e.event_type FROM events e JOIN products p ON p.product_id = e.product_id "
            "WHERE e.user_id = ? AND e.event_type IN ('purchase', 'add_to_cart', 'wishlist') "
            "ORDER BY e.timestamp DESC LIMIT 3", (user_id,)).fetchall()
    verbs = {"purchase": "bought", "add_to_cart": "added to cart", "wishlist": "wishlisted"}
    lines = []
    if user:
        lines.append(f"📍 {user['city']} · age {user['age_group']}")
    lines.append(f"🧾 {orders['n']} past orders · ₹{orders['total']:,.0f} spent")
    lines += [f"• {verbs[r['event_type']]}: {r['title']}" for r in recent]
    return "\n".join(lines)


def main() -> None:
    """python -m bot.personas  ->  (re)build the pool of demo shoppers."""
    import time

    start = time.time()
    counts = prepare_pool()
    for p in PERSONAS:
        print(f"  {p.button:<24} {counts[p.key]} fitting shopper(s)")
    print(f"Persona pool ready in {time.time() - start:.0f} s.")


if __name__ == "__main__":
    main()
