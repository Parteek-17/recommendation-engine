"""Pipeline (Phase 7): trigger detection, the offer pipeline orchestrator and the scheduler.

Contract functions (CLAUDE.md -> Module Contracts -> pipeline):
    detect_triggers(now) -> list[(user_id, trigger_type)]
    run_offer_pipeline(user_id, trigger_type) -> str (status)

The scheduler is NOT imported here (it needs APScheduler); run it with `python -m pipeline.scheduler`.
"""

from pipeline.offer_pipeline import run_offer_pipeline, run_pipeline
from pipeline.triggers import detect_triggers

__all__ = ["detect_triggers", "run_offer_pipeline", "run_pipeline"]
