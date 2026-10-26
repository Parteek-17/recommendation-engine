"""The Offer Pipeline (CLAUDE.md -> "Offer Pipeline (APPROVED)"), one user + one trigger at a time.

    from pipeline import run_offer_pipeline
    run_offer_pipeline("U00004", "cart_abandon")   ->  "sent" | "blocked" | "no_offer" | ...

The steps, in the approved order (each one writes a row to audit_log, component='pipeline'):

    1. eligibility  offers.check_eligibility    consent, Telegram link, quiet hours, caps, segment
       control      rules.yaml control_group_pct: a fixed share of users gets NO offer, so we can
                    measure what offers really add (demo testers are never held out)
       likely_buyer rules.yaml skip_likely_buyers: premium regulars get a reminder, not a discount
    0. prepare      personal triggers: let the Offer Engine create this user's offer
                    (only if the user is otherwise eligible — no coupons for opted-out users)
    2. fetch        offers.fetch_live_offers    Offers API over HTTP (API key stays server-side)
    3. rank         reco.rank_offers_for_user   best offer for THIS user first
    4. guardrails   offers.apply_offer_guardrails
    4b. choose      llm.offer_chooser           Claude PICKS from the rule-approved shortlist (or
                    "no discount"); an answer outside the shortlist is rejected -> rec engine's pick
    5. write        llm.write_offer_message     Claude writes the words only
    6. revalidate   offers.revalidate           still live? price unchanged? in stock?
    7. send         bot.send_offer              Telegram; the bot writes the offer_sends row itself

Any step that says "no" stops the run safely and the reason is in audit_log.
The exceptions are steps 4b and 5: if the LLM fails we use the rec engine's pick / a plain template.
`dry_run=True` runs steps 0-6 and returns the message that WOULD be sent, without sending.
"""

from __future__ import annotations

import threading
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import requests

from bot import send_offer, send_text
from common.config import settings
from common.db import get_connection
from common.logging_utils import audit, get_logger, now_local
from common.schemas import Offer, TriggerType
from llm import catalog as llm_catalog
from llm import client as llm_client
from llm import write_offer_message
from llm.offer_chooser import NO_DISCOUNT, OfferChoice, choose_offer
from llm.templates import discount_text, fallback_offer_message
from offers import apply_offer_guardrails, check_eligibility, fetch_live_offers, revalidate
from offers import rules
from offers.generator import generate_offers
from offers.segments import budget_band
from offers.store import Catalog
from pipeline import store
from reco import build_user_context, rank_offers_for_user

log = get_logger(__name__)

COMPONENT = "pipeline"
NO_LIVE_OFFER = "no live"  # start of check_eligibility's "no live <trigger> offer for this user's segment"


@dataclass
class PipelineResult:
    """What happened in one run. `status` is what run_offer_pipeline() returns."""

    user_id: str
    trigger_type: str
    status: str = "started"
    reason: str = ""
    offer_id: str | None = None
    message: str | None = None
    steps: list[str] = field(default_factory=list)  # steps that passed, in order
    chosen_by: str = ""       # "ai" | "ranker" | "rule": who picked the offer (step 4b)
    choice_reason: str = ""   # why, in plain English (shown on the dashboard)
    ai_rejected: str = ""     # set when the AI's pick broke the rules and was replaced


# Final statuses (anything other than "sent" / "dry_run" / "reminder_sent" means nothing was delivered):
#   invalid_trigger · blocked (eligibility) · holdout (control group) · offers_api_down · no_offer
#   guardrail_blocked · revalidate_failed · dry_run · sent · failed / blocked from the bot's final check
#   reminder_sent / reminder_dry_run (no-discount nudge: likely buyer, or the AI chose "no discount")


def _step(result: PipelineResult, step: str, decision: str, reason: str = "", **details) -> None:
    audit(COMPONENT, result.user_id, f"step_{step}", decision, reason,
          {"trigger_type": result.trigger_type, **details})
    if decision in ("allowed", "ok", "fallback", "dry_run", "sent"):
        result.steps.append(step)


def _stop(result: PipelineResult, status: str, reason: str) -> PipelineResult:
    result.status, result.reason = status, reason
    log.info("pipeline %s/%s stopped: %s (%s)", result.user_id, result.trigger_type, status, reason)
    return result


def offers_api_up() -> bool:
    """Is the Offers API answering? (/health needs no key.)"""
    try:
        return requests.get(settings.OFFERS_API_URL.rstrip("/") + "/health", timeout=2).status_code == 200
    except requests.RequestException:
        return False


def warm_up(user_id: str | None = None) -> None:
    """Pay the one-off start-up costs before the first real run (the scheduler calls this once).

    - LLM client: `import anthropic` + HTTPS setup (~10-15 s, measured 2026-10-26).
    - Offers API: its first /offers/live call for a user loads the rec engine (5-15 s). One
      request now means the first real fetch is fast and can't hit the read timeout.
    Errors are ignored: a cold start is only slower, never wrong.
    """
    llm_client.warm_up()
    if user_id and offers_api_up():
        fetch_live_offers(user_id)


def _start_llm_warm_up() -> threading.Thread | None:
    """Create the Claude client in the background while steps 0-4 run (they don't need it).
    Only the first run in a process does this; later runs reuse the client."""
    if llm_client.is_ready():
        return None
    thread = threading.Thread(target=llm_client.warm_up, name="llm-warm-up", daemon=True)
    thread.start()
    return thread


def template_message(offer: Offer, trigger_type: str) -> str:
    """Plain, safe offer text built only from DB values. llm.write_offer_message already falls
    back to this on any Claude problem; the pipeline also uses it if the llm layer itself breaks."""
    try:
        return fallback_offer_message(offer, trigger_type)
    except Exception:  # even the template helper failed -> the most basic text possible
        return f"A special offer for you! Use code {offer.code} before {offer.expires_at:%d %b %Y}."


def _save_rankings(user_id: str, ranked, now: datetime) -> None:
    """Keep the ranking in the contract table offer_rankings (for the Admin page / evaluation)."""
    stamp = now.isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.executemany("INSERT INTO offer_rankings (user_id, offer_id, score, rank, created_at) VALUES (?, ?, ?, ?, ?)",
                         [(user_id, r.offer_id, float(r.score), i, stamp) for i, r in enumerate(ranked, start=1)])


def _send_status(user_id: str, offer_id: str) -> str:
    """Status of the newest offer_sends row the bot wrote for this user + offer."""
    with get_connection() as conn:
        row = conn.execute("SELECT status FROM offer_sends WHERE user_id = ? AND offer_id = ? "
                           "ORDER BY send_id DESC LIMIT 1", (user_id, offer_id)).fetchone()
    return row["status"] if row else "failed"


def in_control_group(user_id: str) -> bool:
    """Fixed, random-looking split: the same user is always in (or out of) the control group."""
    if rules.CONTROL_GROUP_PCT <= 0 or store.is_tester(user_id):
        return False
    return zlib.crc32(user_id.encode()) % 100 < rules.CONTROL_GROUP_PCT


def likely_buyer(user_id: str, trigger_type: str, now: datetime) -> str | None:
    """Reason text if rules.yaml says this user would probably buy without a discount, else None."""
    rule = rules.SKIP_LIKELY_BUYERS
    if not rule.get("enabled") or trigger_type not in rule.get("triggers", []):
        return None
    band = budget_band(user_id)
    if band not in rule.get("budget_bands", []):
        return None
    orders = store.orders_since(user_id, now - timedelta(days=int(rule["lookback_days"])))
    if orders < int(rule["min_orders"]):
        return None
    return f"likely to buy anyway ({band} budget band, {orders} orders in {rule['lookback_days']} days): no discount needed"


def reminder_message(user_id: str, trigger_type: str) -> str:
    """A friendly nudge WITHOUT a coupon. Product names and prices come from the DB."""
    event = {"cart_abandon": "add_to_cart", "price_drop": "wishlist"}.get(trigger_type)
    product = llm_catalog.get_product(store.open_item(user_id, event)) if event else None
    if product and trigger_type == "cart_abandon":
        return (f"🛒 Your {product['title']} is still saved in your cart "
                f"({llm_catalog.format_inr(product['price'])}). It's ready whenever you are!")
    if product:
        return (f"💖 {product['title']} from your wishlist is now "
                f"{llm_catalog.format_inr(product['price'])}. Take another look?")
    return "✨ We've picked a few new things you might like. Send /recommend to see them!"


def _send_reminder(result: PipelineResult, why: str, now: datetime, dry_run: bool) -> PipelineResult:
    """No discount, just a reminder: costs the store nothing, so no offer rules are needed."""
    message = reminder_message(result.user_id, result.trigger_type)
    result.message = message
    if dry_run:
        _step(result, "send", "dry_run", f"reminder (dry run): {why}", message=message)
        return _stop(result, "reminder_dry_run", why)
    sent = send_text(result.user_id, message, now=now)
    _step(result, "send", "sent" if sent else "failed", f"reminder without discount: {why}", message=message)
    return _stop(result, "reminder_sent" if sent else "failed", why)


def shopper_facts(user_id: str, trigger_type: str, now: datetime) -> dict:
    """Non-personal facts the AI chooser may see (no name, no id, no city)."""
    context = build_user_context(user_id)
    return {
        "budget_band": context.budget_band or "mid",
        "likes": ", ".join(llm_catalog.category_name(c) for c in context.top_categories[:3]),
        "orders_last_120_days": store.orders_since(user_id, now - timedelta(days=120)),
        "reason_for_offer": trigger_type.replace("_", " "),
    }


def candidate_facts(offer: Offer, ranked, catalog: Catalog) -> dict:
    product = llm_catalog.get_product(offer.product_id) if offer.product_id else None
    bundle = llm_catalog.get_product(offer.bundle_product_id) if offer.bundle_product_id else None
    facts = {
        "offer_id": offer.offer_id,
        "what": discount_text(offer),
        "product": product["title"] if product else "",
        "bundled_with": bundle["title"] if bundle else "",
        "category": llm_catalog.category_name(offer.category_id) if offer.category_id else "",
        "min_cart_inr": offer.min_cart_value or 0,
        "est_cost_inr": rules.offer_value_inr(offer, catalog),
        "rec_engine_score": round(float(ranked.score), 3),
        "rec_engine_reason": ranked.reason,
    }
    return {k: v for k, v in facts.items() if v not in ("", None)}


def _choose(result: PipelineResult, safe: list, offers: list[Offer], now: datetime) -> OfferChoice:
    """Step 4b: the AI picks from the shortlist the rules approved; the rules check its answer."""
    shortlist = safe[: int(rules.AI_CHOICE.get("shortlist_size", 3))]
    if not rules.AI_CHOICE.get("enabled"):
        return OfferChoice(shortlist[0].offer_id, "rec engine's top pick (AI choice is off in rules.yaml)", "ranker")
    by_id = {o.offer_id: o for o in offers}
    catalog = Catalog.load()
    candidates = [candidate_facts(by_id[r.offer_id], r, catalog) for r in shortlist]
    return choose_offer(shopper_facts(result.user_id, result.trigger_type, now), candidates, result.user_id)


def _run(user_id: str, trigger_type: str, now: datetime, dry_run: bool) -> PipelineResult:
    rules.refresh_rules()  # pick up any change the marketing team saved in rules.yaml
    result = PipelineResult(user_id, trigger_type)
    try:
        TriggerType(trigger_type)
    except ValueError:
        return _stop(result, "invalid_trigger", f"unknown trigger_type '{trigger_type}'")

    # --- 1. Eligibility (with step 0 tucked inside) ---------------------------------
    check = check_eligibility(user_id, trigger_type, now=now)
    no_offer_yet = not check.allowed and check.reason.startswith(NO_LIVE_OFFER)
    if not (check.allowed or no_offer_yet):  # opted out, quiet hours, caps... -> done, nothing to warm up
        _step(result, "eligibility", "blocked", check.reason)
        return _stop(result, "blocked", check.reason)
    llm_warm_up = _start_llm_warm_up()
    try:
        return _run_steps(result, check, no_offer_yet, now, dry_run, llm_warm_up)
    finally:
        if llm_warm_up:  # never leave the thread running after the run (tests swap the client)
            llm_warm_up.join()


def _run_steps(result: PipelineResult, check, no_offer_yet: bool, now: datetime, dry_run: bool,
               llm_warm_up: threading.Thread | None) -> PipelineResult:
    """Steps 0-7 for a user who passed (or may pass, once an offer exists) step 1."""
    user_id, trigger_type = result.user_id, result.trigger_type

    # --- 1b. Control group: no offer at all, so we can measure what offers add ----------
    if in_control_group(user_id):
        _step(result, "control", "holdout", f"control group ({rules.CONTROL_GROUP_PCT:g}% of users get no offer)")
        return _stop(result, "holdout", "control group: no offer, used to measure uplift")
    # --- 1c. Would they buy anyway? Then a reminder, not a discount -------------------
    why = likely_buyer(user_id, trigger_type, now)
    if why:
        _step(result, "likely_buyer", "ok", why)
        result.chosen_by, result.choice_reason = "rule", why
        return _send_reminder(result, why, now, dry_run)

    personal = trigger_type != TriggerType.FESTIVAL.value
    if no_offer_yet or (check.allowed and personal):
        # The user passed consent / Telegram / quiet hours / caps. Let the Offer Engine make
        # the offer that fits THIS trigger (e.g. 10% off the exact item left in the cart,
        # segment user:<id>). Identical live offers are not created twice. Generic offers
        # for everyone may also be live; the ranker picks the best one in step 3.
        created = generate_offers(trigger_type, user_id, now=now)
        _step(result, "prepare", "ok", f"{len(created)} new offer(s) created",
              offer_ids=[o.offer_id for o in created])
        if no_offer_yet:
            check = check_eligibility(user_id, trigger_type, now=now)
    if not check.allowed:
        _step(result, "eligibility", "blocked", check.reason)
        return _stop(result, "blocked", check.reason)
    _step(result, "eligibility", "allowed", check.reason)

    # --- 2. Fetch live offers from the Offers API --------------------------------------
    offers = [o for o in fetch_live_offers(user_id, trigger_type=trigger_type)
              if o.trigger_type is None or o.trigger_type.value == trigger_type]
    if not offers:
        if not offers_api_up():
            _step(result, "fetch", "blocked", "Offers API is not reachable")
            return _stop(result, "offers_api_down", "Offers API is not reachable")
        _step(result, "fetch", "blocked", "no live offers for this trigger")
        return _stop(result, "no_offer", "no live offers for this trigger")
    _step(result, "fetch", "allowed", f"{len(offers)} live offer(s)")

    # --- 3. Rank them for this user (rec engine) ---------------------------------------
    ranked = rank_offers_for_user(user_id, offers)
    if not ranked:
        _step(result, "rank", "blocked", "rec engine ranked no offers")
        return _stop(result, "no_offer", "rec engine ranked no offers")
    if not dry_run:
        _save_rankings(user_id, ranked, now)
    _step(result, "rank", "allowed", f"best: {ranked[0].offer_id} ({ranked[0].reason})",
          ranking=[r.offer_id for r in ranked[:5]])

    # --- 4. Offer guardrails -------------------------------------------------------------
    safe = apply_offer_guardrails(user_id, ranked, now=now)
    if not safe:
        _step(result, "guardrails", "blocked", "every offer was removed by the guardrails")
        return _stop(result, "guardrail_blocked", "every offer was removed by the guardrails")
    _step(result, "guardrails", "allowed", f"{len(safe)} safe offer(s)",
          safe=[r.offer_id for r in safe[:5]])

    # --- 4b. The AI chooses (only from what the rules approved) ---------------------------
    if llm_warm_up:
        llm_warm_up.join()  # Claude client ready (usually finished while steps 0-4 ran)
        llm_warm_up = None
    choice = _choose(result, safe, offers, now)
    result.chosen_by, result.choice_reason, result.ai_rejected = choice.source, choice.reason, choice.rejected
    _step(result, "choose", "fallback" if choice.rejected else "ok",
          f"{choice.source}: {choice.offer_id} — {choice.reason}", offer_id=choice.offer_id,
          ai_rejected=choice.rejected)
    if choice.offer_id == NO_DISCOUNT:
        return _send_reminder(result, f"AI chose no discount: {choice.reason}", now, dry_run)
    offer = next(o for o in offers if o.offer_id == choice.offer_id)  # send only the chosen one
    result.offer_id = offer.offer_id

    # --- 5. LLM writes the words (never stops the pipeline) ------------------------------
    if llm_warm_up:
        llm_warm_up.join()  # Claude client ready (usually finished while steps 0-4 ran)
    try:
        context = build_user_context(user_id).model_copy(update={"trigger_type": trigger_type})
        message = write_offer_message(offer, context)
        if not message or not message.strip():
            raise ValueError("empty message")
        # write_offer_message never raises: on an LLM problem it returns the template itself.
        used_template = message == template_message(offer, trigger_type)
        _step(result, "write", "fallback" if used_template else "ok",
              "LLM unavailable, template used" if used_template else "message written", offer_id=offer.offer_id)
    except Exception as exc:
        message = template_message(offer, trigger_type)
        _step(result, "write", "fallback", f"LLM layer failed ({type(exc).__name__}), template used",
              offer_id=offer.offer_id)
    result.message = message

    # --- 6. Re-validate right before sending ---------------------------------------------
    final = revalidate(offer.offer_id, user_id, now=now)
    if not final.allowed:
        _step(result, "revalidate", "blocked", final.reason, offer_id=offer.offer_id)
        return _stop(result, "revalidate_failed", final.reason)
    _step(result, "revalidate", "allowed", final.reason, offer_id=offer.offer_id)

    # --- 7. Send (the bot writes offer_sends: queued -> sent / blocked / failed) ----------
    if dry_run:
        _step(result, "send", "dry_run", "dry run: not sent", offer_id=offer.offer_id, message=message)
        return _stop(result, "dry_run", "would send")
    if send_offer(user_id, message, offer.offer_id, now=now):
        _step(result, "send", "sent", "delivered to Telegram", offer_id=offer.offer_id)
        result.status, result.reason = "sent", "delivered to Telegram"
        return result
    status = _send_status(user_id, offer.offer_id)  # 'blocked' (bot's last check) or 'failed'
    _step(result, "send", status, "bot did not deliver the message", offer_id=offer.offer_id)
    return _stop(result, status, "bot did not deliver the message")


def run_pipeline(user_id: str, trigger_type: str, *, now: datetime | None = None,
                 dry_run: bool = False) -> PipelineResult:
    """Run the pipeline and return the full PipelineResult (status, reason, offer, message)."""
    now = now or now_local()
    trigger_type = str(getattr(trigger_type, "value", trigger_type))
    try:
        result = _run(user_id, trigger_type, now, dry_run)
    except Exception as exc:  # a bug in any module must never crash the scheduler
        log.exception("pipeline %s/%s crashed", user_id, trigger_type)
        result = PipelineResult(user_id, trigger_type, status="error", reason=f"{type(exc).__name__}: {exc}")
    audit(COMPONENT, user_id, "run_offer_pipeline", result.status, result.reason,
          {"trigger_type": trigger_type, "offer_id": result.offer_id, "dry_run": dry_run, "steps": result.steps,
           "chosen_by": result.chosen_by, "choice_reason": result.choice_reason, "ai_rejected": result.ai_rejected})
    store.record_run(user_id, trigger_type, result.status, now, dry_run)
    return result


def run_offer_pipeline(user_id: str, trigger_type: str, *, now: datetime | None = None,
                       dry_run: bool = False) -> str:
    """Module-contract function: run the whole pipeline for one user, return the final status."""
    return run_pipeline(user_id, trigger_type, now=now, dry_run=dry_run).status
