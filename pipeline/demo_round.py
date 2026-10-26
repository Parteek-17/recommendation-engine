"""Run one offer round for every classmate playing a demo shopper, and print who got what.

    python -m pipeline.demo_round              # real run: offers go out on Telegram
    python -m pipeline.demo_round --dry-run    # show what WOULD be sent, send nothing

Each tester gets the pipeline for their shopper type's trigger (bot/personas.py). Running it
twice in a day shows the frequency rules at work: the second round is blocked by the daily cap.
"""

from __future__ import annotations

import argparse
import sys

from bot import personas
from bot import store as bot_store
from pipeline.offer_pipeline import run_pipeline


def demo_round(dry_run: bool = False) -> list[dict]:
    rows = []
    for tester in bot_store.list_testers():
        if not tester["active"]:
            continue  # they switched to another shopper (that one has its own row)
        result = run_pipeline(tester["user_id"], tester["trigger_type"], dry_run=dry_run)
        persona = personas.BY_KEY.get(tester["persona"])
        rows.append({
            "shopper": persona.button if persona else tester["persona"],
            "user": tester["user_id"],
            "status": result.status,
            "decided_by": result.chosen_by or "-",
            "why": result.choice_reason or result.reason,
            "message": (result.message or "").replace("\n", " "),
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="don't send anything")
    args = parser.parse_args(argv)
    rows = demo_round(args.dry_run)
    if not rows:
        print("No active testers yet. Share the bot link; anyone who presses Start can pick a shopper.")
        return 0
    for r in rows:
        print(f"\n{r['shopper']}  ({r['user']})  ->  {r['status']}  [decided by: {r['decided_by']}]")
        print(f"   why: {r['why']}")
        if r["message"]:
            print(f"   message: {r['message']}")
    print(f"\n{len(rows)} tester(s). Full detail per step: audit_log, or the Control Room (port 8502).")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # emoji in shopper names on Windows consoles
    sys.exit(main())
