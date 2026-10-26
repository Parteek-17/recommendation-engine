"""Tiny helper the storefront uses to talk to the Main API.

The UI (frontend) never touches the database or the models directly — it only sends
HTTP requests to the API (backend), exactly like a real website or mobile app would.
"""

from __future__ import annotations

import requests

from common.config import settings

TIMEOUT = 60  # seconds; the first request may be slow while models load


class ApiError(RuntimeError):
    """The API answered with an error, or couldn't be reached."""


def _url(path: str) -> str:
    return settings.MAIN_API_URL.rstrip("/") + path


def _handle(response: requests.Response):
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text
        raise ApiError(f"{response.status_code}: {detail}")
    return response.json()


def get(path: str, **params):
    # Drop empty params so e.g. budget_max=None isn't sent as the text "None".
    params = {k: v for k, v in params.items() if v is not None}
    try:
        return _handle(requests.get(_url(path), params=params, timeout=TIMEOUT))
    except requests.ConnectionError:
        raise ApiError(f"Can't reach the API at {settings.MAIN_API_URL}. Is uvicorn running?") from None


def post(path: str, body: dict | None = None):
    try:
        return _handle(requests.post(_url(path), json=body or {}, timeout=TIMEOUT))
    except requests.ConnectionError:
        raise ApiError(f"Can't reach the API at {settings.MAIN_API_URL}. Is uvicorn running?") from None


def put(path: str, body: dict):
    try:
        return _handle(requests.put(_url(path), json=body, timeout=TIMEOUT))
    except requests.ConnectionError:
        raise ApiError(f"Can't reach the API at {settings.MAIN_API_URL}. Is uvicorn running?") from None
