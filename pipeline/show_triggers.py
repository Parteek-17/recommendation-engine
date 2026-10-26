"""Show which users have a trigger right now (read-only, sends nothing).

    python -m pipeline.show_triggers
    python -m pipeline.show_triggers --now 2026-09-30T18:00:00
"""

from __future__ import annotations

import argparse
from datetime import datetime

import pandas as pd

from common.logging_utils import now_local
from pipeline.triggers import PRIORITY, active_festival, detect_triggers


def main() -> None:
    parser = argparse.ArgumentParser(description="Show which users have a trigger right now (read-only).")
    parser.add_argument("--now", help="pretend it is this time, e.g. 2026-09-30T18:00:00")
    args = parser.parse_args()
    now = datetime.fromisoformat(args.now) if args.now else now_local()
    pairs = detect_triggers(now)
    counts = pd.Series([t for _, t in pairs], dtype=object).value_counts()
    print(f"Triggers at {now:%Y-%m-%d %H:%M} (festival: {active_festival(now) or 'none'})")
    for trigger in PRIORITY:
        print(f"  {trigger:<15} {int(counts.get(trigger, 0)):>6} user(s)")
    print(f"  {'total':<15} {len(pairs):>6}")


if __name__ == "__main__":
    main()
