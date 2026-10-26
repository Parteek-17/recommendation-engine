"""Pick the REAL module function if it exists, otherwise the stub from common/stubs.py.

Phases 2–5 and 7 are built in parallel. Until e.g. `reco` exports `get_recommendations`,
the API uses the fake-but-valid stub so everything still works end to end.

`call("get_recommendations", user_id, k=10)` also falls back to the stub if the real
function CRASHES (e.g. models not trained yet) — a demo should never show a 500 error.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable

from common import stubs
from common.logging_utils import get_logger

log = get_logger(__name__)

# function name -> list of module paths to try, in order (first one that has it wins)
_WHERE: dict[str, tuple[str, ...]] = {
    # reco (Phase 2)
    "get_recommendations": ("reco",),
    "get_similar": ("reco",),
    "get_complements": ("reco",),
    "get_trending": ("reco",),
    "build_user_context": ("reco",),
    # llm (Phase 4)
    "chat": ("llm",),
    "check_input": ("llm",),
    "validate_output": ("llm",),
    # bot (Phase 5) — the phase prompt says the deep link lives in bot.linking
    "create_link_token": ("bot.linking", "bot"),
    # pipeline (Phase 7)
    "detect_triggers": ("pipeline",),
    "run_offer_pipeline": ("pipeline",),
}


def _find_real(name: str) -> Callable | None:
    """Return the real function, or None if no module exports it yet."""
    for module_name in _WHERE.get(name, ()):
        try:
            module = importlib.import_module(module_name)
        except Exception:  # module missing or broken -> try the next one / the stub
            continue
        func = getattr(module, name, None)
        if callable(func):
            return func
    return None


def which(name: str) -> str:
    """'real' or 'stub' — shown on /health so you can see which modules are plugged in."""
    return "real" if _find_real(name) else "stub"


def module_status() -> dict[str, str]:
    """One status per module (based on its main function)."""
    return {
        "reco": which("get_recommendations"),
        "llm": which("chat"),
        "bot": which("create_link_token"),
        "pipeline": which("run_offer_pipeline"),
    }


def call(name: str, *args: Any, **kwargs: Any) -> Any:
    """Call the real function; if it is missing or raises, call the stub instead."""
    real = _find_real(name)
    if real is not None:
        try:
            return real(*args, **kwargs)
        except Exception as exc:
            log.warning("Real %s failed (%s) — using stub instead", name, exc)
    return getattr(stubs, name)(*args, **kwargs)
