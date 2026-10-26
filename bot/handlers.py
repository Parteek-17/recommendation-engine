"""What the bot does when a user sends a command, a message, or taps a button.

How a Telegram bot receives things (long polling):
    Our program keeps asking Telegram "anything new?" (getUpdates). Each answer is an
    `Update`: a message ("/start abc", "show me shoes") or a button tap (a callback query).
    python-telegram-bot looks at each Update and calls the matching function below.
    Every handler is `async` because the library runs on asyncio: while one handler
    waits for Telegram or the LLM, others can run.

Safety rules followed here:
- We never show internal errors to users (see `on_error`).
- Prices, titles and offer values come from the DB, never from the LLM's text.
- Free text goes to `llm.chat`, which runs `check_input` on it (the agent's guardrails),
  and each user may send at most 10 chat messages per minute.
- A user can only press buttons on offers that were sent to THEM.
"""

from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from bot import deps, keyboards, personas, store
from bot.keyboards import NOT_INTERESTED_REASONS, QUIET_PRESETS, clip
from bot.linking import link_chat
from bot.sender import ChatRateLimiter
from common.config import settings
from common.logging_utils import audit, get_logger

log = get_logger(__name__)

chat_limiter = ChatRateLimiter(max_messages=10, window_s=60)

MAX_CARDS = 3  # product cards after a chat reply
RECOMMEND_K = 5

HELP_TEXT = (
    "Hi! I'm your shopping assistant 🛍\n\n"
    "/recommend – my top picks for you (with reasons)\n"
    "/offers – your active offers\n"
    "/settings – quiet hours, offers per week, favourite categories\n"
    "/gift – turn gift mode on/off (picks for someone else)\n"
    "/switch – play a different demo shopper\n"
    "/stop – stop all offers\n"
    "/help – this message\n\n"
    "Or just type what you're looking for, e.g. \"running shoes under ₹3000\"."
)
NOT_LINKED_TEXT = (
    "I don't know which shop account is yours yet 🙂\n"
    "Open the shop website → \"Get offers on Telegram\" and tap the link there. "
    "It brings you back here and links your account."
)
SORRY_TEXT = "Sorry, something went wrong on our side. Please try again in a minute."
PERSONA_TEXT = (
    "Welcome to the Parvaaz offer engine demo! 👋\n\n"
    "Our shop's customers are synthetic, so pick a shopper to play. You'll take over their "
    "shopping history and get the offer our system would send them — chosen by AI, checked "
    "against the store's rules.\n\nWho do you want to be?"
)


async def _reply(update: Update, text: str, markup=None) -> None:
    await update.effective_message.reply_text(clip(text), reply_markup=markup)


def _user(update: Update) -> str | None:
    return store.user_for_chat(update.effective_chat.id)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start <token> links the account; /start alone opts back in or explains how to link."""
    chat_id = update.effective_chat.id
    if context.args:
        result = link_chat(context.args[0], chat_id)
        messages = {
            "linked": "✅ Your account is linked and offers are switched on.\n\n" + HELP_TEXT,
            "already_linked": "You're already linked 👍\n\n" + HELP_TEXT,
            "expired": "This link has expired (links work for 24 hours). Please get a new one from the website.",
            "used": "This link was already used. Please get a new one from the website.",
            "invalid": "This link isn't valid. Please get a new one from the website.",
        }
        await _reply(update, messages[result.reason])
        return

    user_id = _user(update)
    if user_id is None:  # a classmate without a shop account: let them play a demo shopper
        await _reply(update, PERSONA_TEXT, keyboards.persona_keyboard(personas.PERSONAS))
        return
    if not store.is_opted_in(user_id):
        store.set_opt_in(user_id, True)
        await _reply(update, "Welcome back! Offers are switched on again. Send /stop any time.\n\n" + HELP_TEXT)
        return
    await _reply(update, HELP_TEXT)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Opt out immediately. sender.pre_send_check reads opted_in before every send."""
    user_id = _user(update)
    if user_id is None:
        await _reply(update, "You're not linked, so you won't get any offers from me.")
        return
    store.set_opt_in(user_id, False)
    await _reply(update, "🔕 Done — you won't get any more offers. You can still chat with me.\n"
                         "Send /start to switch offers back on.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, HELP_TEXT)


async def cmd_switch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pick another demo shopper (each one shows a different kind of offer)."""
    await _reply(update, "Who do you want to be next?", keyboards.persona_keyboard(personas.PERSONAS))


async def _persona_button(update: Update, key: str) -> None:
    """A tester picked a shopper type: link them to a fitting synthetic user, then run the
    real offer pipeline for that user right away, and explain what happened."""
    persona = personas.BY_KEY.get(key)
    if persona is None:
        return
    from pipeline.offer_pipeline import run_pipeline  # lazy: the pipeline imports this package

    await update.callback_query.edit_message_reply_markup(None)
    await _reply(update, f"⏳ Setting you up as {persona.button}…")
    user_id = await asyncio.to_thread(personas.assign, persona.key, update.effective_chat.id)
    if user_id is None:
        await _reply(update, "Sorry, every demo shopper is taken right now. Please try another type.")
        return
    summary = await asyncio.to_thread(personas.profile_summary, user_id)
    await _reply(update, f"You are now {persona.story}.\n\n{summary}\n\n"
                         f"🎯 What should happen: {persona.expect}.\n⏳ Our system is deciding your offer…")
    result = await asyncio.to_thread(run_pipeline, user_id, persona.trigger_type)
    await _reply(update, personas.explain_result(result) + "\n\nTry /switch for another shopper, or /help.")


async def cmd_gift(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = _user(update)
    if user_id is None:
        await _reply(update, NOT_LINKED_TEXT)
        return
    on = not store.get_gift_mode(user_id)
    store.set_gift_mode(user_id, on)
    if on:
        await _reply(update, "🎁 Gift mode ON — /recommend now shows gift ideas and won't change your "
                             "own taste profile. Send /gift again to turn it off.")
    else:
        await _reply(update, "Gift mode OFF — back to picks for you.")


async def cmd_recommend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = _user(update)
    if user_id is None:
        await _reply(update, NOT_LINKED_TEXT)
        return
    gift = store.get_gift_mode(user_id)
    user_ctx = await asyncio.to_thread(deps.build_user_context, user_id)
    user_ctx = user_ctx.model_copy(update={"is_gift_mode": gift})
    recs = await asyncio.to_thread(deps.get_recommendations, user_id, RECOMMEND_K, user_ctx)
    await _send_cards(update, [(r.product_id, r.reason) for r in recs],
                      "🎁 Gift ideas:" if gift else "✨ Top picks for you:")


async def cmd_offers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = _user(update)
    if user_id is None:
        await _reply(update, NOT_LINKED_TEXT)
        return
    sends = store.active_sends_for_user(user_id)
    if not sends:
        await _reply(update, "You have no active offers right now. I'll message you when there's one for you!")
        return
    await _reply(update, f"🎟 Your active offers ({len(sends)}):")
    for s in sends:
        await _reply(update, _offer_text(s), keyboards.offer_keyboard(s["send_id"]))


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = _user(update)
    if user_id is None:
        await _reply(update, NOT_LINKED_TEXT)
        return
    consent = store.get_consent(user_id) or {}
    cats = store.top_level_categories()
    await _reply(update, keyboards.settings_text(consent, cats), keyboards.settings_keyboard(consent, cats))


# ---------------------------------------------------------------------------
# Free-text chat
# ---------------------------------------------------------------------------


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _chat(update, update.effective_message.text or "")


async def _chat(update: Update, text: str) -> None:
    user_id = _user(update)
    if user_id is None:
        await _reply(update, NOT_LINKED_TEXT)  # don't spend LLM money on unknown people
        return
    if not chat_limiter.allow(user_id):
        audit("bot", user_id, "chat", "blocked", "chat rate limit")
        await _reply(update, "You're typing faster than I can shop 😅 Please wait a minute and try again.")
        return
    # llm.chat is a normal (blocking) function: run it in a thread so the bot stays responsive.
    reply = await asyncio.to_thread(deps.chat, user_id, text)
    await _reply(update, reply.text, keyboards.quick_reply_keyboard(reply.buttons))
    await _send_cards(update, [(pid, None) for pid in reply.product_ids[:MAX_CARDS]], header=None)


async def _send_cards(update: Update, items: list[tuple[str, str | None]], header: str | None) -> None:
    """One message per product, details from the DB. Unknown product IDs are skipped (grounding)."""
    cards = [(store.get_product(pid), reason) for pid, reason in items]
    cards = [(p, reason) for p, reason in cards if p is not None]
    if header:
        await _reply(update, header if cards else "I couldn't find picks for you right now. Try again later!")
    for product, reason in cards:
        await _reply(update, keyboards.product_card(product, reason), keyboards.product_keyboard(product["product_id"]))


# ---------------------------------------------------------------------------
# Buttons (callback queries)
# ---------------------------------------------------------------------------


def _offer_text(offer: dict) -> str:
    product = store.get_product(offer.get("product_id"))
    what = keyboards.describe_offer(offer, store.category_name(offer.get("category_id")),
                                    product["title"] if product else "")
    expires = str(offer.get("expires_at") or "")[:10]
    return f"🎟 {what}\nCode: {offer['code']}  ·  valid till {expires}"


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # stops the little loading spinner on the button
    if (query.data or "").startswith("t:"):  # demo shopper choice works before linking
        await _persona_button(update, query.data[2:])
        return
    user_id = _user(update)
    if user_id is None:
        await _reply(update, NOT_LINKED_TEXT)
        return
    parts = (query.data or "").split(":")
    kind = parts[0]
    if kind == "p" and len(parts) == 3:
        await _product_button(update, user_id, parts[1], parts[2])
    elif kind == "o" and len(parts) == 3:
        await _offer_button(update, user_id, parts[1], parts[2])
    elif kind == "n" and len(parts) == 4:
        await _reason_button(update, user_id, parts[1], parts[2], parts[3])
    elif kind == "s" and len(parts) == 3:
        await _settings_button(update, user_id, parts[1], parts[2])
    elif kind == "b":
        await _quick_reply(update, query.data[2:])
    else:
        log.warning("Unknown button data from %s: %r", user_id, query.data)


async def _product_button(update: Update, user_id: str, action: str, product_id: str) -> None:
    product = store.get_product(product_id)
    if product is None:
        await _reply(update, "Sorry, that product is no longer available.")
        return
    if action == "v":
        store.log_event(user_id, product_id, "click")
        text = keyboards.product_card(product) + "\n\n" + (product.get("description") or "")
        complements = await asyncio.to_thread(deps.get_complements, product_id, 3)
        titles = [p["title"] for p in (store.get_product(r.product_id) for r in complements) if p]
        if titles:
            text += "\n\n🧩 Goes well with: " + ", ".join(titles)
        await _reply(update, text)
    elif action == "n":
        await update.callback_query.edit_message_reply_markup(keyboards.reasons_keyboard("p", product_id))


async def _offer_button(update: Update, user_id: str, action: str, send_id: str) -> None:
    send = store.get_send(send_id)
    if send is None or send["user_id"] != user_id:  # only the person it was sent to may use it
        await _reply(update, "Sorry, this offer isn't available.")
        return
    offer = store.get_offer(send["offer_id"])
    if offer is None:
        await _reply(update, "Sorry, this offer isn't available any more.")
        return
    product_id = offer.get("product_id") or offer.get("bundle_product_id")

    if action == "v":
        store.set_send_status(send_id, "clicked")
        if product_id:
            store.log_event(user_id, product_id, "click")
        text = _offer_text(offer)
        product = store.get_product(product_id)
        if product:
            text += "\n\n" + keyboards.product_card(product)
        await _reply(update, text)
    elif action == "a":
        check = await asyncio.to_thread(deps.revalidate, offer["offer_id"], user_id)
        if not check.allowed:
            audit("bot", user_id, "apply_offer", "blocked", check.reason, {"send_id": send_id})
            await _reply(update, "Sorry, this offer has expired or is no longer active.")
            return
        store.set_send_status(send_id, "redeemed")
        if product_id:
            store.log_event(user_id, product_id, "add_to_cart")
        audit("bot", user_id, "apply_offer", "redeemed", "Apply offer button", {"send_id": send_id})
        await _reply(update, f"✅ Code {offer['code']} is applied to your cart. Happy shopping!")
    elif action == "n":
        store.set_send_status(send_id, "opened")  # they saw it, but didn't click through
        await update.callback_query.edit_message_reply_markup(keyboards.reasons_keyboard("o", send_id))


async def _reason_button(update: Update, user_id: str, code: str, kind: str, item_id: str) -> None:
    if code not in NOT_INTERESTED_REASONS:
        return
    reason = NOT_INTERESTED_REASONS[code][1]
    if kind == "p":
        store.record_not_interested(user_id, reason, product_id=item_id)
    elif kind == "o":
        send = store.get_send(item_id)
        if send is None or send["user_id"] != user_id:
            return
        offer = store.get_offer(send["offer_id"]) or {}
        store.record_not_interested(user_id, reason, product_id=offer.get("product_id"), offer_id=send["offer_id"])
    await update.callback_query.edit_message_reply_markup(None)  # remove the buttons
    await _reply(update, "Thanks for telling me — I'll show you fewer things like this. 👍")


async def _settings_button(update: Update, user_id: str, action: str, value: str) -> None:
    consent = store.get_consent(user_id) or {}
    if action == "q":
        preset = next(((s, e) for _, s, e in QUIET_PRESETS
                       if s.replace(":", "") + e.replace(":", "") == value), None)
        if preset is None:
            return
        store.update_consent(user_id, quiet_start=preset[0], quiet_end=preset[1])
    elif action == "m" and value.isdigit():
        store.update_consent(user_id, max_offers_per_week=max(1, min(int(value), settings.MAX_OFFERS_PER_WEEK)))
    elif action == "c":
        if value not in dict(store.top_level_categories()):
            return
        favourites = [c for c in (consent.get("preferred_categories") or "").split("|") if c]
        favourites = [c for c in favourites if c != value] if value in favourites else favourites + [value]
        store.update_consent(user_id, preferred_categories="|".join(favourites))
    else:
        return
    audit("bot", user_id, "settings", "updated", f"{action}={value}")
    consent = store.get_consent(user_id) or {}
    cats = store.top_level_categories()
    await update.callback_query.edit_message_text(keyboards.settings_text(consent, cats),
                                                  reply_markup=keyboards.settings_keyboard(consent, cats))


async def _quick_reply(update: Update, label: str) -> None:
    """Quick-reply buttons act like the user typed the label."""
    if label.strip().lower() == "show more":
        await cmd_recommend(update, None)
    else:
        await _chat(update, label)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the real error for developers; the user only sees a polite sorry."""
    log.error("Error while handling an update", exc_info=context.error)
    audit("bot", None, "handle_update", "error", type(context.error).__name__)
    message = getattr(update, "effective_message", None)  # some updates (e.g. polls) have no message
    if message is not None:
        try:
            await message.reply_text(SORRY_TEXT)
        except Exception:
            pass
