"""The Offer Pipeline (CLAUDE.md -> "Offer Pipeline (APPROVED)"), one user + one trigger at a time.

    from pipeline import run_offer_pipeline
    run_offer_pipeline("U00004", "cart_abandon")   ->  "sent" | "blocked" | "no_offer" | ...

The steps, in the approved order (each one writes a row to audit_log, component='pipeline'):

    0. prepare      personal triggers: let the Offer Engine create this user's offer
                    (only if the user is otherwise eligible — no coupons for opted-out users)
    1. eligibility  offers.check_eligibility    consent, Telegram link, quiet hours, caps, segment
    2. fetch        offers.fetch_live_offers    Offers API over HTTP (API key stays server-side)
    3. rank         reco.rank_offers_for_user   best offer for THIS user first
    4. guardrails   offers.apply_offer_guardrails
    5. write        llm.write_offer_message     Claude writes the words only
    6. revalidate   offers.revalidate           still live? price unchanged? in stock?
    7. send         bot.send_offer              Telegram; the bot writes the offer_sends row itself

Any step that says "no" stops the run safely and the reason is in audit_log.
The ONE exception is step 5: if the LLM fails we send a plain template message instead.
`dry_run=True` runs steps 0-6 and returns the message that WOULD be sent, without sending.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime

import requests

from bot import send_offer
from common.config import settings
from common.db import get_connection
from common.logging_utils import audit, get_logger, now_local
from common.schemas import Offer, TriggerType
from llm import client as llm_client
from llm import write_offer_message
from llm.templates import fallback_offer_message
from offers import apply_offer_guardrails, check_eligibility, fetch_live_offers, revalidate
from offers.generator import generate_offers
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


# Final statuses (anything other than "sent" / "dry_run" means nothing was delivered):
#   invalid_trigger · blocked (eligibility) · offers_api_down · no_offer · guardrail_blocked
#   revalidate_failed · dry_run · sent · failed / blocked from the bot's final check


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


def _run(user_id: str, trigger_type: str, now: datetime, dry_run: bool) -> PipelineResult:
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
    offer = next(o for o in offers if o.offer_id == safe[0].offer_id)  # send only the best one
    result.offer_id = offer.offer_id
    _step(result, "guardrails", "allowed", f"{len(safe)} safe offer(s), using {offer.offer_id}",
          offer_id=offer.offer_id)

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
          {"trigger_type": trigger_type, "offer_id": result.offer_id, "dry_run": dry_run, "steps": result.steps})
    store.record_run(user_id, trigger_type, result.status, now, dry_run)
    return result


def run_offer_pipeline(user_id: str, trigger_type: str, *, now: datetime | None = None,
                       dry_run: bool = False) -> str:
    """Module-contract function: run the whole pipeline for one user, return the final status."""
    return run_pipeline(user_id, trigger_type, now=now, dry_run=dry_run).status
