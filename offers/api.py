"""Offers API — our own small web service that serves offers.

Run it:
    uvicorn offers.api:app --port 8001
Then open http://127.0.0.1:8001/docs for clickable, auto-generated docs.

Every route except /health needs the header  X-API-Key: <OFFERS_API_KEY from .env>.
Wrong or missing key -> 401. The key is only ever used server-side (pipeline -> this API),
never sent to a browser, the Telegram bot or the LLM.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from common.config import ConfigError, settings
from common.logging_utils import get_logger
from common.schemas import Offer, TriggerType
from offers import store
from offers.generator import generate_offers
from offers.segments import list_live_offers

log = get_logger(__name__)


def require_api_key(x_api_key: str | None = Header(default=None, description="Must equal OFFERS_API_KEY")) -> None:
    """Dependency that protects every route below. Never logs the key itself."""
    try:
        expected = settings.require("OFFERS_API_KEY")  # also refuses the public placeholder 'change-me'
    except ConfigError:
        log.error("OFFERS_API_KEY is not set (or still 'change-me') — refusing all requests. See docs/SETUP_GUIDE.md")
        raise HTTPException(status_code=503, detail="Offers API is not configured (OFFERS_API_KEY missing)")
    # compare_digest takes the same time whether the first or last character is wrong,
    # so an attacker can't guess the key letter by letter from response times.
    if not x_api_key or not secrets.compare_digest(x_api_key.encode(), expected.encode()):
        log.warning("Rejected request with %s API key", "missing" if not x_api_key else "wrong")
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


class GenerateRequest(BaseModel):
    trigger_type: TriggerType | None = Field(default=None, description="Leave empty to run all six triggers")
    user_id: str | None = Field(default=None, description="Make personal offers for this user only")
    save: bool = Field(default=True, description="False = dry run (nothing saved)")


app = FastAPI(
    title="Offers API",
    description="Serves live offers created by our Offer Engine. Send header `X-API-Key` on every call except /health.",
    version="1.0",
)
router = APIRouter(dependencies=[Depends(require_api_key)])


@app.get("/health", tags=["system"])
def health() -> dict:
    """Is the service up? (No key needed, so monitoring tools can call it.)"""
    return {"status": "ok"}


# NOTE: /offers/live must be declared before /offers/{offer_id}, otherwise "live" would be read as an offer_id.
@router.get("/offers/live", response_model=list[Offer], tags=["offers"])
def get_live_offers(
    user_id: str | None = Query(default=None, description="Only offers whose segment includes this user"),
    trigger_type: TriggerType | None = Query(default=None),
) -> list[Offer]:
    """Active, unexpired offers. Per-user guardrails (caps, dedup, budget) are applied later by the pipeline."""
    return list_live_offers(user_id, trigger_type.value if trigger_type else None)


@router.get("/offers/{offer_id}", response_model=Offer, tags=["offers"])
def get_offer(offer_id: str) -> Offer:
    offer = store.get_offer(offer_id)
    if offer is None:
        raise HTTPException(status_code=404, detail=f"Offer {offer_id} not found")
    return offer


@router.post("/offers/generate", response_model=list[Offer], tags=["offers"])
def post_generate(body: GenerateRequest) -> list[Offer]:
    """Run the rule-based generator. Returns only the newly created offers."""
    return generate_offers(body.trigger_type.value if body.trigger_type else None, body.user_id, save=body.save)


app.include_router(router)
