"""Client for the Offers API (step 3 of the Offer Pipeline).

    from offers import fetch_live_offers
    offers = fetch_live_offers("U00001")    # list[Offer], or [] if the API is down

Safety rules:
  - The API key comes from .env via common.config and is sent only in the request header.
    It is never logged or put in the audit log.
  - If the API can't be reached we return [] — NEVER made-up offers — and write audit_log.
"""

from __future__ import annotations

import time
from pathlib import Path

import requests
from requests.exceptions import ReadTimeout

from common.config import ConfigError, settings
from common.logging_utils import audit, get_logger
from common.schemas import Offer

log = get_logger(__name__)

# (connect, read). The read limit is generous on purpose: the API's FIRST /offers/live call
# loads the rec engine (5-15 s on a busy laptop). With 10 s we used to time out, retry and make
# the server do the same slow work twice (~14 s lost in one measured run).
TIMEOUT_SECONDS = (3, 30)
RETRIES = 3                 # total attempts (a read timeout is not retried, see below)
BACKOFF_SECONDS = 0.5       # waits 0.5 s, then 1 s, then 2 s ...

_sleep = time.sleep  # tests replace this so they don't actually wait


def _fail(user_id: str | None, reason: str, db_path) -> list[Offer]:
    log.error("fetch_live_offers failed: %s", reason)
    audit("offers", user_id, "fetch_live_offers", "failed", reason, {"url": settings.OFFERS_API_URL}, db_path=db_path)
    return []


def fetch_live_offers(user_id: str | None = None, *, trigger_type: str | None = None,
                      db_path: str | Path | None = None) -> list[Offer]:
    """Module-contract function: GET /offers/live from our Offers API, with timeout + retry."""
    try:
        api_key = settings.require("OFFERS_API_KEY")
    except ConfigError as exc:
        return _fail(user_id, f"config error: {exc}", db_path)

    url = settings.OFFERS_API_URL.rstrip("/") + "/offers/live"
    params = {k: v for k, v in {"user_id": user_id, "trigger_type": trigger_type}.items() if v}
    last_error = "unknown error"
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(url, params=params, headers={"X-API-Key": api_key}, timeout=TIMEOUT_SECONDS)
            if resp.status_code == 200:
                return [Offer(**o) for o in resp.json()]
            last_error = f"HTTP {resp.status_code}"
            if resp.status_code < 500:  # 401/404/422: retrying won't help
                break
        except ReadTimeout:  # the server got the request but is stuck: asking again only queues more work
            last_error = "ReadTimeout"
            break
        except requests.RequestException as exc:  # connection refused, connect timeout, ...
            last_error = f"{type(exc).__name__}"
        except (ValueError, TypeError) as exc:  # bad JSON or an offer that doesn't fit the schema
            last_error = f"invalid response: {exc}"
            break
        if attempt < RETRIES:
            log.warning("Offers API attempt %d/%d failed (%s), retrying...", attempt, RETRIES, last_error)
            _sleep(BACKOFF_SECONDS * 2 ** (attempt - 1))
    return _fail(user_id, f"Offers API unavailable after {attempt} attempt(s): {last_error}", db_path)
