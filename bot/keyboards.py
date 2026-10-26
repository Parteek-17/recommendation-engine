"""Inline buttons and the text of product / offer cards.

How inline buttons work: each button carries a short hidden string (`callback_data`,
max 64 bytes). When the user taps it, Telegram sends that string back to our bot as a
"callback query", and `handlers.on_button` decides what to do.

Our callback_data format is "<kind>:<action>:<id>":
    p:v:P00001        product  -> View
    p:n:P00001        product  -> Not interested (then asks why)
    o:v:SN000012      offer    -> View         (id = offer_sends.send_id)
    o:a:SN000012      offer    -> Apply offer
    o:n:SN000012      offer    -> Not interested
    n:<reason>:p:P00001 / n:<reason>:o:SN000012   -> the "why" answer
    s:q:2208 / s:m:3 / s:c:C001                    -> /settings choices
    b:<label>                                      -> quick-reply button from send_text / chat
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from common.config import settings

# reason code -> label. Codes are short to fit the 64-byte limit; the reco engine reads the label.
NOT_INTERESTED_REASONS = {
    "ab": ("Already bought", "already_bought"),
    "te": ("Too expensive", "too_expensive"),
    "ns": ("Not my style", "not_my_style"),
}

# (button label, quiet_start, quiet_end)
QUIET_PRESETS = [("21:00–07:00", "21:00", "07:00"), ("22:00–08:00", "22:00", "08:00"),
                 ("23:00–07:00", "23:00", "07:00")]

MAX_CALLBACK_BYTES = 64
MAX_MESSAGE_CHARS = 4000  # Telegram's hard limit is 4096


def _fit(data: str) -> str:
    """Cut callback data to Telegram's 64-byte limit without breaking a UTF-8 character."""
    raw = data.encode("utf-8")[:MAX_CALLBACK_BYTES]
    return raw.decode("utf-8", errors="ignore")


def clip(text: str) -> str:
    return text if len(text) <= MAX_MESSAGE_CHARS else text[: MAX_MESSAGE_CHARS - 1] + "…"


def rupees(value) -> str:
    try:
        return f"₹{float(value):,.0f}"
    except (TypeError, ValueError):
        return "₹?"


# ---------------------------------------------------------------------------
# Cards (plain text on purpose: no Markdown/HTML parsing, so text from the LLM or the
# catalog can never break formatting or inject links)
# ---------------------------------------------------------------------------


def product_card(product: dict, reason: str | None = None) -> str:
    lines = [f"🛍 {product['title']}"]
    price = rupees(product.get("price"))
    mrp = product.get("mrp")
    if mrp and float(mrp) > float(product.get("price") or 0):
        price += f"  (MRP {rupees(mrp)})"
    details = [d for d in (product.get("brand"), price) if d]
    if product.get("avg_rating"):
        details.append(f"⭐ {float(product['avg_rating']):.1f}")
    lines.append(" · ".join(details))
    if reason:
        lines.append(f"💡 {reason}")
    return "\n".join(lines)


def describe_offer(offer: dict, category: str = "", product_title: str = "") -> str:
    """One-line human description of what the offer gives (values from the DB)."""
    value = float(offer.get("value") or 0)
    kind = offer.get("offer_type")
    what = {
        "pct_off": f"{value:g}% off",
        "flat_off": f"{rupees(value)} off",
        "free_delivery": "Free delivery",
        "bundle": f"{value:g}% off the bundle",
        "buy_again": f"{value:g}% off when you buy again",
        "points": f"{value:g} bonus points",
    }.get(kind, "Special offer")
    target = product_title or category
    if target:
        what += f" on {target}"
    min_cart = float(offer.get("min_cart_value") or 0)
    if min_cart > 0:
        what += f" (min cart {rupees(min_cart)})"
    return what


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------


def product_keyboard(product_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🛒 View", callback_data=f"p:v:{product_id}"),
        InlineKeyboardButton("👎 Not interested", callback_data=f"p:n:{product_id}"),
    ]])


def offer_keyboard(send_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 View", callback_data=f"o:v:{send_id}"),
         InlineKeyboardButton("🎟 Apply offer", callback_data=f"o:a:{send_id}")],
        [InlineKeyboardButton("👎 Not interested", callback_data=f"o:n:{send_id}")],
    ])


def reasons_keyboard(kind: str, item_id: str) -> InlineKeyboardMarkup:
    """The 'why not?' follow-up. kind = 'p' (product) or 'o' (offer send)."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(label, callback_data=f"n:{code}:{kind}:{item_id}")
        for code, (label, _) in NOT_INTERESTED_REASONS.items()
    ]])


def quick_reply_keyboard(labels: list[str]) -> InlineKeyboardMarkup | None:
    """Buttons from send_text(buttons=[...]) or ChatReply.buttons. Tapping one = sending that text."""
    labels = [label for label in labels if label and label.strip()][:6]
    if not labels:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(label[:40], callback_data=_fit(f"b:{label}"))]
                                 for label in labels])


def settings_keyboard(consent: dict, categories: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """/settings menu. A ✅ marks the current choice."""
    rows = []
    q_now = (consent.get("quiet_start"), consent.get("quiet_end"))
    rows.append([InlineKeyboardButton(("✅ " if (s, e) == q_now else "") + label,
                                      callback_data=f"s:q:{s.replace(':', '')}{e.replace(':', '')}")
                 for label, s, e in QUIET_PRESETS])
    current_max = int(consent.get("max_offers_per_week") or settings.MAX_OFFERS_PER_WEEK)
    # Users may lower the weekly cap, never raise it above the system limit.
    choices = range(1, max(1, min(settings.MAX_OFFERS_PER_WEEK, 7)) + 1)
    rows.append([InlineKeyboardButton(("✅ " if n == current_max else "") + f"{n}/week",
                                      callback_data=f"s:m:{n}") for n in choices])
    favourites = set(filter(None, (consent.get("preferred_categories") or "").split("|")))
    buttons = [InlineKeyboardButton(("✅ " if cid in favourites else "") + name, callback_data=f"s:c:{cid}")
               for cid, name in categories]
    rows += [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


def settings_text(consent: dict, categories: list[tuple[str, str]]) -> str:
    names = dict(categories)
    favourites = [names.get(c, c) for c in (consent.get("preferred_categories") or "").split("|") if c]
    return (
        "⚙️ Your settings\n"
        f"🌙 Quiet hours: {consent.get('quiet_start') or settings.QUIET_START}–"
        f"{consent.get('quiet_end') or settings.QUIET_END} (no offers then)\n"
        f"📬 Max offers per week: {consent.get('max_offers_per_week') or settings.MAX_OFFERS_PER_WEEK}\n"
        f"❤️ Favourite categories: {', '.join(favourites) or 'none yet'}\n\n"
        "Tap a button to change. Row 1 = quiet hours, row 2 = offers per week, the rest = categories."
    )
