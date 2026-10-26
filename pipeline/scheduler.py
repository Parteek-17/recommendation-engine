"""Scheduler: every 15 minutes, find triggers and run the offer pipeline for each.

    python -m pipeline.scheduler                 # live: runs now, then every 15 min (Ctrl+C to stop)
    python -m pipeline.scheduler --dry-run       # prints what WOULD be sent, sends nothing
    python -m pipeline.scheduler --once          # one round, then exit
    python -m pipeline.scheduler --once --dry-run --limit 20

One round (`run_round`):
  1. snapshot product prices (so the price_drop trigger can notice changes)
  2. detect_triggers(now)
  3. keep only users we can reach at all (opted in + Telegram linked). The rest are
     counted in ONE summary audit row instead of thousands of "not opted in" rows.
  4. skip (user, trigger) pairs already handled in the last 24 h (cooldown)
  5. run the offer pipeline for each pair, most urgent trigger first

Also: every night at 03:00 the rec engine is retrained (`python -m reco.train`), because
CF / ALS only learn new products and patterns when retrained (phases/NOTES.md).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from datetime import datetime

from common.config import PROJECT_ROOT
from common.logging_utils import audit, get_logger, now_local
from pipeline import store
from pipeline.offer_pipeline import run_pipeline, warm_up
from pipeline.triggers import active_festival, detect_triggers

log = get_logger(__name__)

INTERVAL_MINUTES = 15
RETRAIN_HOUR = 3  # 03:00 local time, when the shop is quiet


def run_round(now: datetime | None = None, dry_run: bool = False, limit: int | None = None,
              verbose: bool = True) -> Counter:
    """One full round. Returns a Counter of final statuses (plus 'unreachable' / 'cooldown')."""
    now = now or now_local()
    added = store.snapshot_prices(now)
    pairs = detect_triggers(now)
    reachable = store.reachable_users()
    done_recently = set() if dry_run else store.recently_run(now)

    todo = [(u, t) for u, t in pairs if u in reachable and (u, t) not in done_recently]
    stats = Counter(unreachable=sum(u not in reachable for u, _ in pairs),
                    cooldown=sum(u in reachable and (u, t) in done_recently for u, t in pairs))
    if limit is not None:
        todo = todo[:limit]
    if todo:
        warm_up(todo[0][0])  # start-up costs paid once here, not inside the first user's run

    if verbose:
        mode = "DRY RUN — nothing will be sent" if dry_run else "LIVE"
        print(f"\n=== Offer round {now:%Y-%m-%d %H:%M} ({mode}) ===")
        print(f"festival: {active_festival(now) or 'none'} · price changes recorded: {added}")
        print(f"triggers: {len(pairs)} · reachable (opted in + Telegram): {len(todo) + stats['cooldown']} "
              f"· skipped unreachable: {stats['unreachable']} · cooldown: {stats['cooldown']}")

    for user_id, trigger in todo:
        result = run_pipeline(user_id, trigger, now=now, dry_run=dry_run)
        stats[result.status] += 1
        if verbose:
            line = f"  {user_id} {trigger:<15} -> {result.status:<18} {result.reason}"
            if result.offer_id:
                line += f" [{result.offer_id}]"
            print(line)
            if dry_run and result.message:
                print(f"      would send: {result.message}")

    audit("pipeline", None, "scheduler_round", "dry_run" if dry_run else "done",
          f"{len(pairs)} trigger(s), {len(todo)} processed", {"stats": dict(stats), "now": now.isoformat()})
    if verbose:
        print("result: " + (", ".join(f"{k}={v}" for k, v in sorted(stats.items())) or "nothing to do"))
    return stats


def retrain_reco() -> None:
    """Nightly retrain in a separate process (keeps the scheduler's memory small).
    Other running processes (API, bot) pick up the new models on their next restart."""
    log.info("Nightly reco retrain starting...")
    code = subprocess.call([sys.executable, "-m", "reco.train"], cwd=PROJECT_ROOT)
    audit("pipeline", None, "retrain_reco", "done" if code == 0 else "failed", f"exit code {code}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Offer scheduler: detect triggers and run the offer pipeline.")
    parser.add_argument("--dry-run", action="store_true", help="print what would be sent, send nothing")
    parser.add_argument("--once", action="store_true", help="run one round and exit")
    parser.add_argument("--limit", type=int, help="process at most this many triggers per round")
    parser.add_argument("--now", help="--once only: pretend it is this time (e.g. 2026-09-28T18:00:00)")
    parser.add_argument("--no-retrain", action="store_true", help="don't retrain the rec engine at night")
    args = parser.parse_args()

    if args.now and not args.once:
        parser.error("--now only works together with --once")
    if args.once:
        run_round(datetime.fromisoformat(args.now) if args.now else None, args.dry_run, args.limit)
        return

    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler(timezone=_tz())
    scheduler.add_job(run_round, "interval", minutes=INTERVAL_MINUTES, next_run_time=datetime.now(_tz()),
                      kwargs={"dry_run": args.dry_run, "limit": args.limit},
                      id="offer_round", max_instances=1, coalesce=True)
    if not args.no_retrain:
        scheduler.add_job(retrain_reco, "cron", hour=RETRAIN_HOUR, id="retrain_reco", max_instances=1)
    print(f"Scheduler started: offer round every {INTERVAL_MINUTES} min"
          f"{'' if args.no_retrain else f', reco retrain daily at {RETRAIN_HOUR:02d}:00'}"
          f"{' (DRY RUN)' if args.dry_run else ''}. Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Scheduler stopped.")


def _tz():
    from zoneinfo import ZoneInfo

    from common.config import settings

    return ZoneInfo(settings.TIMEZONE)


if __name__ == "__main__":
    main()
