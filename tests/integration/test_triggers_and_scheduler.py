"""Trigger detection (all 6 types), the scheduler round, and "no stubs in production paths"."""

from __future__ import annotations

import importlib
import pkgutil
from datetime import datetime, timedelta

from common.logging_utils import now_local
from pipeline import detect_triggers, store
from pipeline.scheduler import run_round
from pipeline.triggers import active_festival
from tests.integration.conftest import add_event, execute, link_telegram, query

USER = "U00010"


def pairs_for(user_id: str, now: datetime) -> set[str]:
    return {t for u, t in detect_triggers(now) if u == user_id}


# ---------------------------------------------------------------------------
# detect_triggers: one test per trigger type
# ---------------------------------------------------------------------------


def test_cart_abandon_needs_two_idle_hours():
    now = now_local()
    add_event(USER, "P00033", "add_to_cart", now - timedelta(minutes=20))
    assert "cart_abandon" not in pairs_for(USER, now)            # still shopping
    assert "cart_abandon" in pairs_for(USER, now + timedelta(hours=3))
    add_event(USER, "P00033", "purchase", now + timedelta(hours=4))
    assert "cart_abandon" not in pairs_for(USER, now + timedelta(hours=5))  # bought it after all


def test_sample_cart_abandon_is_found():
    # Planted in the sample data: U00004 adds shoes on 2026-09-28 and never buys.
    assert "cart_abandon" in pairs_for("U00004", datetime(2026, 9, 29, 12, 0))


def test_price_drop_on_wishlisted_item():
    now = now_local()
    store.snapshot_prices(now - timedelta(days=2))                # price we know about
    add_event(USER, "P00039", "wishlist", now - timedelta(days=1))
    assert "price_drop" not in pairs_for(USER, now)
    execute("UPDATE products SET price = price * 0.8 WHERE product_id = 'P00039'")
    store.snapshot_prices(now)
    assert "price_drop" in pairs_for(USER, now)


def test_repurchase_due_for_consumable():
    # P00004 atta, repurchase_days = 30. Bought 29 days ago -> due tomorrow.
    now = now_local()
    add_event(USER, "P00004", "purchase", now - timedelta(days=29))
    assert "repurchase_due" in pairs_for(USER, now)
    assert "repurchase_due" not in pairs_for(USER, now - timedelta(days=20))  # not due yet


def test_post_purchase_with_known_complements():
    now = now_local()
    add_event(USER, "P00001", "purchase", now - timedelta(days=1))  # phone -> covers are complements
    assert "post_purchase" in pairs_for(USER, now)
    assert "post_purchase" not in pairs_for(USER, now + timedelta(days=5))


def test_win_back_after_30_quiet_days():
    last = query("SELECT MAX(timestamp) AS t FROM events WHERE user_id = 'U00012'")[0]["t"]
    last = datetime.fromisoformat(last)
    assert "win_back" not in pairs_for("U00012", last + timedelta(days=10))
    assert "win_back" in pairs_for("U00012", last + timedelta(days=35))


def test_festival_window_for_opted_in_users():
    diwali = datetime(2026, 11, 1, 12, 0)
    assert active_festival(diwali) == "Diwali"
    users = {u for u, t in detect_triggers(diwali) if t == "festival"}
    opted_in = {r["user_id"] for r in query("SELECT user_id FROM user_consent WHERE opted_in = 1")}
    assert users == opted_in
    assert active_festival(datetime(2026, 9, 1, 12, 0)) is None


def test_triggers_sorted_most_urgent_first():
    from pipeline.triggers import PRIORITY

    found = detect_triggers(datetime(2026, 11, 1, 12, 0))
    order = [PRIORITY.index(t) for _, t in found]
    assert order == sorted(order)


# ---------------------------------------------------------------------------
# Scheduler round
# ---------------------------------------------------------------------------


def test_scheduler_round_live_then_cooldown(system):
    link_telegram(USER, 700000010)
    add_event(USER, "P00033", "add_to_cart", now_local() - timedelta(hours=3))

    first = run_round(verbose=False)
    assert first["sent"] >= 1
    assert any(m["chat_id"] == "700000010" for m in system.telegram.sent)
    assert first["unreachable"] > 0      # e.g. sample users who never linked Telegram

    sent_before = len(system.telegram.sent)
    second = run_round(verbose=False)
    assert second["cooldown"] >= 1       # same (user, trigger) is not processed again for 24 h
    assert len(system.telegram.sent) == sent_before


def test_scheduler_dry_run_sends_nothing(system, capsys):
    link_telegram(USER, 700000010)
    add_event(USER, "P00033", "add_to_cart", now_local() - timedelta(hours=3))
    stats = run_round(dry_run=True)
    out = capsys.readouterr().out
    assert stats["dry_run"] >= 1 and "would send:" in out and "DRY RUN" in out
    assert system.telegram.sent == []
    assert query("SELECT COUNT(*) AS n FROM offer_sends WHERE user_id = ?", (USER,))[0]["n"] == 0


# ---------------------------------------------------------------------------
# No stubs left in production code paths
# ---------------------------------------------------------------------------

PRODUCTION_PACKAGES = ["reco", "offers", "llm", "bot", "api", "pipeline"]


def test_no_production_module_uses_a_stub():
    """Every contract function a production module imported must be the real one, not common.stubs."""
    using_stubs = []
    for package in PRODUCTION_PACKAGES:
        pkg = importlib.import_module(package)
        for info in pkgutil.walk_packages(pkg.__path__, prefix=f"{package}."):
            if info.name.endswith(("scheduler", "main")) and package == "bot":
                continue
            module = importlib.import_module(info.name)
            for name, obj in vars(module).items():
                if callable(obj) and getattr(obj, "__module__", "") == "common.stubs":
                    using_stubs.append(f"{info.name}.{name}")
    assert using_stubs == []


def test_api_reports_all_modules_real():
    from api.modules import module_status

    assert set(module_status().values()) == {"real"}
