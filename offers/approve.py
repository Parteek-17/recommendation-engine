"""Admin approval for high-value offers (worth more than APPROVAL_THRESHOLD_INR).

    python -m offers.approve                  # list pending offers and decide one by one
    python -m offers.approve --list           # only list them
    python -m offers.approve --approve OF0013
    python -m offers.approve --reject OF0013

Approve -> requires_approval = 0 (the guardrails will now let it through).
Reject  -> is_active = 0 (it will never be sent).
Both are written to audit_log.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from common.logging_utils import audit
from common.schemas import Offer
from offers import store
from offers.rules import offer_value_inr


def pending_offers(db_path: str | Path | None = None) -> list[Offer]:
    """Active offers still waiting for a human decision."""
    return [o for o in store.load_offers(db_path) if o.requires_approval and o.is_active]


def decide(offer_id: str, approve: bool, db_path: str | Path | None = None) -> bool:
    """Approve or reject one pending offer. Returns False if it isn't pending."""
    if offer_id not in {o.offer_id for o in pending_offers(db_path)}:
        return False
    store.set_approval(offer_id, approve, db_path)
    audit("offers", None, "approve_offer", "approved" if approve else "rejected",
          "admin decision", {"offer_id": offer_id}, db_path=db_path)
    return True


def _describe(o: Offer, catalog: store.Catalog) -> str:
    # "Rs" not "₹": the classic Windows console can't print the rupee sign and would crash.
    target = o.product_id or o.category_id or "whole cart"
    return (f"{o.offer_id}  {o.code:<16} {o.offer_type.value:<10} value={o.value:<6g} on {target:<8} "
            f"~Rs {offer_value_inr(o, catalog):,.0f} per use  segment={o.segment}  expires {o.expires_at:%d %b %Y}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Approve or reject high-value offers.")
    parser.add_argument("--list", action="store_true", help="only list pending offers")
    parser.add_argument("--approve", metavar="OFFER_ID")
    parser.add_argument("--reject", metavar="OFFER_ID")
    args = parser.parse_args()

    for offer_id, ok in ((args.approve, True), (args.reject, False)):
        if offer_id:
            done = decide(offer_id, ok)
            print(f"{offer_id}: {'approved' if ok else 'rejected'}" if done else f"{offer_id} is not a pending offer.")
    if args.approve or args.reject:
        return

    pending, catalog = pending_offers(), store.Catalog.load()
    if not pending:
        print("No offers are waiting for approval.")
        return
    print(f"{len(pending)} offer(s) waiting for approval:\n")
    for o in pending:
        print(_describe(o, catalog))
    if args.list:
        return
    print()
    for o in pending:
        answer = input(f"{o.offer_id}: [a]pprove / [r]eject / [s]kip? ").strip().lower()
        if answer in ("a", "r"):
            decide(o.offer_id, answer == "a")
            print("  ->", "approved" if answer == "a" else "rejected")


if __name__ == "__main__":
    main()
