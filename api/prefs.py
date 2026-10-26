"""User controls ("My controls" page): budget, Familiar <-> Explore, gift mode, "not interested".

The Data Contract has no table for these, and `common/` is owned by Phase 0, so the API
keeps them in a small JSON file next to the database: data/user_prefs.json.
(See phases/NOTES.md — if a `user_prefs` table is added later, only this file changes.)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from pydantic import BaseModel, Field

from common.config import settings

_LOCK = threading.Lock()  # uvicorn may serve two requests at once; don't let them both write the file


class UserPrefs(BaseModel):
    """Everything the user told us directly (as opposed to what we learned from clicks)."""

    budget_max: float | None = Field(default=None, description="Hide products above this price (₹)")
    explore: float = Field(default=0.0, ge=0.0, le=1.0, description="0 = familiar only, 1 = lots of new things")
    gift_mode: bool = False
    hidden_products: list[str] = Field(default_factory=list)
    hidden_brands: list[str] = Field(default_factory=list)
    hidden_categories: list[str] = Field(default_factory=list)
    reset_at: str | None = Field(default=None, description="When the user pressed 'Reset my profile'")


def prefs_path() -> Path:
    # Computed on every call (not at import) so tests can point settings.DB_PATH at a temp folder.
    return settings.DB_PATH.parent / "user_prefs.json"


def _read_all() -> dict[str, dict]:
    path = prefs_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}  # a broken file shouldn't take the whole API down


def get_prefs(user_id: str) -> UserPrefs:
    with _LOCK:
        return UserPrefs(**_read_all().get(user_id, {}))


def save_prefs(user_id: str, prefs: UserPrefs) -> UserPrefs:
    with _LOCK:
        data = _read_all()
        data[user_id] = prefs.model_dump()
        path = prefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return prefs
