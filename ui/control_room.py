"""Offer Control Room — the marketing team's dashboard (Streamlit).

    streamlit run ui/control_room.py --server.port 8502

Three pages, written for people who don't code:
  📏 Offer rules   change every limit with sliders; a live example shows what a change does
  📈 Results       is it working? offers sent, used, money spent vs extra purchases, why things were blocked
  🧪 Demo testers  the classmates playing demo shoppers on Telegram, and what each one received

It reads the database directly (it runs on the store's own server, next to the pipeline)
and writes only offers/rules.yaml, through offers.rules.save_rules (which validates first).
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # `streamlit run` only adds ui/ to the import path

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from common.db import get_connection  # noqa: E402
from offers import rules  # noqa: E402

st.set_page_config(page_title="Offer Control Room", page_icon="🎯", layout="wide")

TRIGGER_LABELS = {
    "cart_abandon": "🛒 Left items in cart",
    "price_drop": "💖 Wishlist price drop",
    "repurchase_due": "🔁 Time to re-order",
    "post_purchase": "📦 Just bought something",
    "win_back": "😴 Hasn't shopped in 30+ days",
    "festival": "🪔 Festival sale",
}
LIVE_FROM = "2026-10-01"  # the synthetic data window ends on 30 Sep 2026
BAND_LABELS = {"low": "Budget shopper (price-sensitive)", "mid": "Regular shopper", "high": "Premium shopper"}


def query(sql: str, params=()) -> pd.DataFrame:
    with get_connection() as conn:
        try:
            return pd.read_sql_query(sql, conn, params=params)
        except Exception:  # table not created yet (e.g. no tester has joined)
            return pd.DataFrame()


# ---------------------------------------------------------------------------
# 📏 Offer rules
# ---------------------------------------------------------------------------


def example_discount(r: dict, trigger: str, band: str, price: float, margin_pct: float) -> tuple[float, str]:
    """Same sizing as offers/rules.py, using the values on screen (before saving)."""
    start = r["starting_discount_pct"][trigger]
    mult = r["price_sensitivity_multiplier"][band]
    cap = r["limits"]["max_discount_pct"]
    floor_room = max(0.0, margin_pct - r["limits"]["min_margin_after_discount_pct"])
    raw = start * mult
    pct = int(min(raw, cap, floor_room))
    if raw <= min(cap, floor_room):
        why = f"{start}% starting discount × {mult} for a {BAND_LABELS[band].lower()}"
    elif pct == int(cap):
        why = f"capped at the maximum discount ({cap}%)"
    else:
        why = (f"limited by profit: this product earns {margin_pct:g}%, and we must keep "
               f"{r['limits']['min_margin_after_discount_pct']:g}%")
    return pct, why


def page_rules() -> None:
    st.title("📏 Offer rules")
    st.write("These are the limits for **every** offer. Claude may choose and word offers, but each choice "
             "is checked against these rules before anything is sent. Change a value, look at the example "
             "on the right, then press **Save rules**. Changes apply on the next offer run.")
    if rules.last_error:
        st.error(f"The rules file has a mistake, so the last good rules are still in use: {rules.last_error}")

    saved = rules.load_rules()
    r = copy.deepcopy(saved)
    left, right = st.columns([3, 2], gap="large")

    with left:
        st.subheader("💰 Money limits")
        c1, c2 = st.columns(2)
        r["limits"]["max_discount_pct"] = c1.slider(
            "Biggest discount allowed (%)", 0, 90, int(saved["limits"]["max_discount_pct"]),
            help="No offer is ever bigger than this.")
        r["limits"]["min_margin_after_discount_pct"] = c2.slider(
            "Profit we always keep (% of price)", 0.0, 20.0, float(saved["limits"]["min_margin_after_discount_pct"]), 0.5,
            help="After any discount the product must still earn at least this much.")
        r["limits"]["approval_threshold_inr"] = c1.number_input(
            "Offers worth more than ₹ … need a manager's OK", 0, 1_000_000,
            int(saved["limits"]["approval_threshold_inr"]), step=100)
        r["limits"]["daily_discount_budget_inr"] = c2.number_input(
            "Total ₹ we may give away per day", 0, 100_000_000, int(saved["limits"]["daily_discount_budget_inr"]), step=5000)

        st.subheader("📬 Don't annoy customers")
        c1, c2 = st.columns(2)
        r["frequency"]["max_offers_per_day"] = c1.slider("Offers per customer per day", 0, 5,
                                                         int(saved["frequency"]["max_offers_per_day"]))
        r["frequency"]["max_offers_per_week"] = c2.slider("Offers per customer per week", 0, 14,
                                                          int(saved["frequency"]["max_offers_per_week"]))
        hours = [f"{h:02d}:00" for h in range(24)]
        r["frequency"]["quiet_start"] = c1.selectbox("Quiet hours start", hours,
                                                     index=hours.index(saved["frequency"]["quiet_start"])
                                                     if saved["frequency"]["quiet_start"] in hours else 22)
        r["frequency"]["quiet_end"] = c2.selectbox("Quiet hours end", hours,
                                                   index=hours.index(saved["frequency"]["quiet_end"])
                                                   if saved["frequency"]["quiet_end"] in hours else 8)

        st.subheader("🎚️ Discount by reason")
        st.caption("The discount we start from. It is then adjusted for the shopper type and never "
                   "goes past the money limits above.")
        cols = st.columns(3)
        for i, (trigger, label) in enumerate(TRIGGER_LABELS.items()):
            col = cols[i % 3]
            r["starting_discount_pct"][trigger] = col.number_input(
                f"{label} — start %", 0, 90, int(saved["starting_discount_pct"][trigger]), key=f"start_{trigger}")
            r["offer_valid_days"][trigger] = col.number_input(
                "valid for (days)", 1, 60, int(saved["offer_valid_days"][trigger]), key=f"days_{trigger}")

        st.subheader("👥 Adjust for the shopper type")
        cols = st.columns(3)
        for col, band in zip(cols, ("low", "mid", "high")):
            r["price_sensitivity_multiplier"][band] = col.slider(
                f"{BAND_LABELS[band]} ×", 0.0, 3.0, float(saved["price_sensitivity_multiplier"][band]), 0.05,
                key=f"mult_{band}")

        st.subheader("🧠 Smart rules")
        skip = r["skip_likely_buyers"]
        skip["enabled"] = st.toggle("Don't give discounts to people who would buy anyway",
                                    bool(saved["skip_likely_buyers"]["enabled"]),
                                    help="They get a friendly reminder without a coupon instead.")
        c1, c2 = st.columns(2)
        skip["min_orders"] = c1.number_input("…if they ordered at least (times)", 1, 100,
                                             int(saved["skip_likely_buyers"]["min_orders"]))
        skip["lookback_days"] = c2.number_input("…in the last (days)", 1, 3650,
                                                int(saved["skip_likely_buyers"]["lookback_days"]))
        skip["budget_bands"] = c1.multiselect("…and they are a", list(BAND_LABELS),
                                              saved["skip_likely_buyers"]["budget_bands"], format_func=BAND_LABELS.get)
        skip["triggers"] = c2.multiselect("…for these reasons", list(TRIGGER_LABELS),
                                          saved["skip_likely_buyers"]["triggers"], format_func=TRIGGER_LABELS.get)
        r["control_group_pct"] = st.slider(
            "Control group: % of customers who get no offer (to measure what offers really add)",
            0, 50, int(saved["control_group_pct"]))
        c1, c2 = st.columns(2)
        r["ai_choice"]["enabled"] = c1.toggle("Let Claude choose between rule-approved offers",
                                              bool(saved["ai_choice"]["enabled"]))
        r["ai_choice"]["shortlist_size"] = c2.slider("How many approved offers Claude may choose from", 1, 10,
                                                     int(saved["ai_choice"]["shortlist_size"]))

    with right:
        st.subheader("🔎 Try an example")
        st.caption("Uses the values on the left, before you save.")
        trigger = st.selectbox("Reason for the offer", list(TRIGGER_LABELS), format_func=TRIGGER_LABELS.get)
        band = st.selectbox("Shopper", list(BAND_LABELS), format_func=BAND_LABELS.get)
        price = st.number_input("Product price (₹)", 50, 200_000, 2000, step=100)
        margin = st.slider("Product profit margin (%)", 1.0, 60.0, 25.0, 0.5)
        pct, why = example_discount(r, trigger, band, price, margin)
        st.metric("Discount this shopper would get", f"{pct}%  ·  ₹{price * pct / 100:,.0f} off")
        st.caption(why)
        if (skip["enabled"] and band in skip["budget_bands"] and trigger in skip["triggers"]):
            st.info(f"If this shopper ordered {skip['min_orders']}+ times in the last {skip['lookback_days']} days, "
                    "they get a reminder **without** a discount.")
        if price * pct / 100 > r["limits"]["approval_threshold_inr"]:
            st.warning("Worth more than the approval limit: a manager must approve it before it is sent.")

        st.divider()
        changed = r != saved
        if st.button("💾 Save rules", type="primary", disabled=not changed, use_container_width=True):
            problems = rules.save_rules(r)
            if problems:
                st.error("Not saved:\n\n" + "\n".join(f"- {p}" for p in problems))
            else:
                st.success("Saved. The next offer run uses the new rules.")
                st.rerun()
        st.caption("Unsaved changes." if changed else "No changes.")
        with st.expander("Advanced: the rules file"):
            st.code(rules.RULES_FILE.read_text(encoding="utf-8"), language="yaml")


# ---------------------------------------------------------------------------
# 📈 Results
# ---------------------------------------------------------------------------


def _pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "–"


def page_results() -> None:
    st.title("📈 Results")
    st.write("Are our offers worth the money? The key question: **did the offer make someone buy who "
             "otherwise wouldn't?** A discount for someone who'd buy anyway is lost profit.")

    st.subheader("1 · Does our engine beat simpler strategies?")
    eval_file = ROOT / "pipeline" / "offer_eval.json"
    if eval_file.exists():
        ev = json.loads(eval_file.read_text(encoding="utf-8"))
        s = ev["strategies"]
        eng, mx = s["engine"], s["max_discount"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Extra purchases per 1,000 customers", f"{eng['incremental_per_1000']:.1f}",
                  f"{eng['incremental_per_1000'] / mx['incremental_per_1000']:.1f}× 'biggest discount for all'",
                  delta_color="off")
        c2.metric("Discount cost per extra purchase", f"₹{eng['cost_per_incremental_inr']:.0f}",
                  f"vs ₹{mx['cost_per_incremental_inr']:.0f} for 'biggest discount for all'", delta_color="off")
        c3.metric("Customers tested (simulation)", f"{ev['evaluated']:,}")
        names = {"engine": "Our engine", "random": "Random offer", "max_discount": "Biggest discount for all",
                 "generic": "Same generic offer for all"}
        table = pd.DataFrame([
            {"Strategy": names.get(k, k), "Extra purchases / 1,000": v["incremental_per_1000"],
             "Discount ₹ / 1,000 customers": v["cost_per_1000_inr"], "₹ per extra purchase": v["cost_per_incremental_inr"]}
            for k, v in s.items()])
        st.bar_chart(table, x="Strategy", y="Extra purchases / 1,000", color="#2a6fdb", horizontal=True, height=220)
        st.dataframe(table, hide_index=True, use_container_width=True)
        st.caption(f"Simulated on the synthetic store as of {ev['now'][:10]}: the same customers, the same "
                   "rule-safe offers, four ways of choosing. 'Extra' = purchases above what the same customers "
                   "do without any offer. Re-run with `python -m pipeline.evaluate_offers`.")
    else:
        st.caption("Run `python -m pipeline.evaluate_offers` to fill this section.")

    st.subheader("2 · Offers delivered")
    sends = query("SELECT status, trigger_type, sent_at FROM offer_sends")
    delivered = sends[sends["status"].isin(["sent", "opened", "clicked", "redeemed"])] if not sends.empty else sends
    # The synthetic history ends on 30 Sep; anything later was sent live by this system.
    live = delivered[delivered["sent_at"].fillna("") >= LIVE_FROM] if not delivered.empty else delivered
    history = delivered[delivered["sent_at"].fillna("") < LIVE_FROM] if not delivered.empty else delivered
    runs = query("SELECT status, COUNT(*) AS n FROM pipeline_runs WHERE dry_run = 0 GROUP BY status")
    run_count = dict(zip(runs.get("status", []), runs.get("n", [])))
    for title, part, extra in (("**Live on Telegram** (this system, since 1 Oct)", live, True),
                               ("**Synthetic history** (Apr–Sep, the generated store data)", history, False)):
        st.markdown(title)
        n = len(part)
        used = int((part["status"] == "redeemed").sum()) if n else 0
        opened = int(part["status"].isin(["opened", "clicked", "redeemed"]).sum()) if n else 0
        c = st.columns(5)
        c[0].metric("Offers delivered", f"{n:,}")
        c[1].metric("Opened", _pct(opened, n), help="Tapped a button on the offer")
        c[2].metric("Used the coupon", _pct(used, n))
        if extra:
            c[3].metric("Reminders without discount", int(run_count.get("reminder_sent", 0)),
                        help="Shoppers who would buy anyway: no money spent")
            c[4].metric("In the control group", int(run_count.get("holdout", 0)),
                        help="Got nothing on purpose, for comparison")
    if not history.empty:
        by_trigger = history.assign(used=history["status"] == "redeemed").groupby("trigger_type").agg(
            delivered=("status", "size"), used=("used", "sum")).reset_index()
        by_trigger["reason"] = by_trigger["trigger_type"].map(TRIGGER_LABELS)
        by_trigger["use rate"] = (100 * by_trigger["used"] / by_trigger["delivered"]).round(1).astype(str) + "%"
        with st.expander("Synthetic history by reason"):
            st.dataframe(by_trigger[["reason", "delivered", "used", "use rate"]], hide_index=True,
                         use_container_width=True)

    st.subheader("3 · Who decided? Claude vs. the rules")
    ai = query("SELECT decision, reason, details_json, timestamp FROM audit_log WHERE action = 'choose_offer' "
               "ORDER BY id DESC LIMIT 200")
    if ai.empty:
        st.caption("No AI choices yet.")
    else:
        accepted, rejected = int((ai["decision"] == "allowed").sum()), int((ai["decision"] == "rejected").sum())
        c1, c2 = st.columns(2)
        c1.metric("Claude's choices accepted by the rules", accepted)
        c2.metric("Claude's choices overruled by the rules", rejected,
                  help="Claude proposed something outside the approved list (or didn't answer): "
                       "the rec engine's rule-approved pick was used instead.")
        recent = ai.head(15).rename(columns={"decision": "rules said", "reason": "Claude's reason / why overruled"})
        recent["rules said"] = recent["rules said"].map({"allowed": "✅ accepted", "rejected": "🛡 overruled"})
        st.dataframe(recent[["timestamp", "rules said", "Claude's reason / why overruled"]], hide_index=True,
                     use_container_width=True)

    st.subheader("4 · Why offers were NOT sent")
    blocks = query("SELECT reason FROM audit_log WHERE (action = 'offer_guardrail' AND decision = 'blocked') "
                   "OR (component = 'pipeline' AND action = 'step_eligibility' AND decision = 'blocked')")
    if blocks.empty:
        st.caption("Nothing blocked yet.")
    else:
        reason = blocks["reason"].str.replace(r"OF\d+", "another offer", regex=True).str.replace(r"\(.*\)", "", regex=True)
        counts = reason.str.strip().value_counts().reset_index()
        counts.columns = ["Rule that stopped it", "Times"]
        st.dataframe(counts.head(12), hide_index=True, use_container_width=True)
        st.caption("Each line is a rule from the Offer rules page doing its job, e.g. 'would stack with another "
                   "offer' = the shopper already holds an unused coupon for the same items.")


# ---------------------------------------------------------------------------
# 🧪 Demo testers
# ---------------------------------------------------------------------------


def page_testers() -> None:
    from bot import personas

    st.title("🧪 Demo testers")
    st.write("Classmates who opened the Telegram bot and picked a demo shopper. Each shopper type shows "
             "a different kind of offer decision.")
    testers = query(
        "SELECT t.user_id, t.persona, t.trigger_type, t.assigned_at, COALESCE(l.active, 0) AS linked "
        "FROM tester_personas t LEFT JOIN telegram_links l ON l.user_id = t.user_id ORDER BY t.assigned_at DESC")
    if testers.empty:
        st.info("No testers yet. Share the bot link: anyone who presses Start can pick a shopper.")
        return
    rows = []
    for t in testers.itertuples():
        run = query("SELECT status FROM pipeline_runs WHERE user_id = ? AND dry_run = 0 ORDER BY run_id DESC LIMIT 1",
                    (t.user_id,))
        send = query("SELECT status, message FROM offer_sends WHERE user_id = ? ORDER BY send_id DESC LIMIT 1",
                     (t.user_id,))
        choice = query("SELECT details_json FROM audit_log WHERE user_id = ? AND action = 'run_offer_pipeline' "
                       "ORDER BY id DESC LIMIT 1", (t.user_id,))
        details = json.loads(choice["details_json"].iloc[0]) if not choice.empty else {}
        persona = personas.BY_KEY.get(t.persona)
        rows.append({
            "Shopper type": persona.button if persona else t.persona,
            "Synthetic user": t.user_id,
            "Outcome": run["status"].iloc[0] if not run.empty else "–",
            "Decided by": details.get("chosen_by") or "–",
            "Why": details.get("choice_reason") or "",
            "Offer status": send["status"].iloc[0] if not send.empty else "–",
            "Message": send["message"].iloc[0] if not send.empty else "",
            "Still playing": "yes" if t.linked else "switched",
        })
    df = pd.DataFrame(rows)
    c = st.columns(4)
    c[0].metric("Testers", len(df))
    c[1].metric("Different shopper types", df["Shopper type"].nunique())
    c[2].metric("Offers or reminders delivered", int(df["Outcome"].isin(["sent", "reminder_sent"]).sum()))
    c[3].metric("Coupons used", int((df["Offer status"] == "redeemed").sum()))
    st.dataframe(df, hide_index=True, use_container_width=True,
                 column_config={"Message": st.column_config.TextColumn(width="large"),
                                "Why": st.column_config.TextColumn(width="medium")})


PAGES = {"📏 Offer rules": page_rules, "📈 Results": page_results, "🧪 Demo testers": page_testers}


def main() -> None:
    rules.refresh_rules()
    with st.sidebar:
        st.header("🎯 Offer Control Room")
        page = st.radio("Page", list(PAGES), label_visibility="collapsed")
        st.caption("For the marketing team. Every limit here applies to the AI too.")
    PAGES[page]()


main()
