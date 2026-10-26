"""Storefront demo UI (Phase 6) — a Streamlit web page that shows off our differentiators.

What is a frontend?  The part people see and click. This file draws the pages; every
piece of data comes from the Main API (backend) over HTTP — see ui/api_client.py.

Run:   uvicorn api.main:app --port 8000      (first, in one terminal)
       streamlit run ui/app.py              (then, in a second terminal)

Every view / click / cart action is sent to POST /events, so the system learns live.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

# `streamlit run ui/app.py` only puts ui/ on the import path; add the project root too.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from common.logging_utils import now_local  # noqa: E402
from ui import api_client as api  # noqa: E402
from ui.api_client import ApiError  # noqa: E402

st.set_page_config(page_title="Parvaaz Store", page_icon="🛍️", layout="wide")

PAGES = ["🏠 Home", "📦 Product", "🛒 Cart", "🎛️ My controls", "💬 Chat", "📊 Admin / Insights"]
NOT_INTERESTED = {
    "too_expensive": "💸 Too expensive",
    "already_have": "✅ Already have one",
    "not_my_style": "🙅 Not my style (brand)",
    "not_relevant": "🚫 Not relevant to me",
}


# ---------------------------------------------------------------------------
# Session state = memory that survives Streamlit's re-run on every click
# ---------------------------------------------------------------------------


def init_state() -> None:
    defaults = {"page": PAGES[0], "session_id": None, "cart": {},
                "product_id": None, "chat": [], "flash": None}
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def flash(message: str, icon: str = "✅") -> None:
    """Show a message after the next re-run (callbacks can't draw on the page directly)."""
    st.session_state.flash = (message, icon)


def safe(func, *args, **kwargs):
    """Call the API; on error remember the message instead of crashing the page."""
    try:
        return func(*args, **kwargs)
    except ApiError as exc:
        flash(str(exc), "⚠️")
        return None


def new_session(is_gift: bool = False) -> None:
    """A shopping session groups events (gift sessions are kept apart from the user's own taste)."""
    res = safe(api.post, "/sessions", {"user_id": st.session_state.user_id, "is_gift": is_gift})
    st.session_state.session_id = res["session_id"] if res else None


def log_event(event_type: str, source: str, product_id: str | None = None, search_query: str | None = None) -> None:
    body = {"user_id": st.session_state.user_id, "event_type": event_type, "source": source,
            "product_id": product_id, "session_id": st.session_state.session_id, "search_query": search_query}
    res = safe(api.post, "/events", body)
    if res:
        st.session_state.session_id = res["session_id"]


# ---------------------------------------------------------------------------
# Button callbacks (run BEFORE the page re-draws, so the new state shows immediately)
# ---------------------------------------------------------------------------


def open_product(product_id: str, source: str) -> None:
    log_event("click", source, product_id)
    st.session_state.product_id = product_id
    st.session_state.page = "📦 Product"
    st.session_state.viewed = None  # makes the product page log one 'view'


def add_to_cart(product_id: str, source: str) -> None:
    cart = st.session_state.cart
    cart[product_id] = cart.get(product_id, 0) + 1
    log_event("add_to_cart", source, product_id)
    flash("Added to cart")


def remove_from_cart(product_id: str) -> None:
    st.session_state.cart.pop(product_id, None)
    log_event("remove_from_cart", "home", product_id)


def wishlist(product_id: str) -> None:
    log_event("wishlist", "product_page", product_id)
    flash("Saved to wishlist ❤️")


def checkout() -> None:
    items = [{"product_id": pid, "quantity": qty} for pid, qty in st.session_state.cart.items()]
    res = safe(api.post, "/orders", {"user_id": st.session_state.user_id, "items": items,
                                      "session_id": st.session_state.session_id})
    if res:
        st.session_state.cart = {}
        flash(f"Order {res['order_id']} placed — ₹{res['total_amount']:,.0f}. "
              "Check Home: your recommendations just switched to accessories!", "🎉")


def not_interested(product_id: str, reason: str) -> None:
    res = safe(api.post, f"/users/{st.session_state.user_id}/not_interested",
               {"product_id": product_id, "reason": reason})
    if res:
        flash("Got it. " + res["effect"], "👍")


def change_user() -> None:
    st.session_state.cart = {}
    st.session_state.chat = []
    st.session_state.product_id = None
    new_session()


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------


def price_line(item: dict) -> str:
    text = f"**₹{item['price']:,.0f}**"
    if item.get("mrp") and item["mrp"] > item["price"]:
        off = round(100 * (1 - item["price"] / item["mrp"]))
        text += f"  ~~₹{item['mrp']:,.0f}~~  {off}% off"
    return text


def product_card(item: dict, source: str, key: str, allow_feedback: bool = False) -> None:
    """One product tile. The 💡 line is the explanation — every recommendation has one."""
    with st.container(border=True):
        st.markdown(f"**{item['title']}**")
        st.caption(f"{item.get('brand', '')} · {item.get('category_name', '')} · ⭐ {item.get('avg_rating', '–')}")
        st.markdown(price_line(item))
        if item.get("reason"):
            st.info(item["reason"], icon="💡")
        c1, c2 = st.columns(2)
        c1.button("View", key=f"view-{key}", on_click=open_product, args=(item["product_id"], source),
                  use_container_width=True)
        c2.button("Add 🛒", key=f"cart-{key}", on_click=add_to_cart, args=(item["product_id"], source),
                  use_container_width=True)
        if allow_feedback:
            with st.popover("Not interested…", use_container_width=True):
                st.caption("Why? Each answer changes what you see.")
                for reason, label in NOT_INTERESTED.items():
                    st.button(label, key=f"ni-{reason}-{key}", on_click=not_interested,
                              args=(item["product_id"], reason), use_container_width=True)


def product_row(items: list[dict], source: str, key: str, per_row: int = 5, allow_feedback: bool = False) -> None:
    if not items:
        st.caption("Nothing to show here yet.")
        return
    for start in range(0, len(items), per_row):
        cols = st.columns(per_row)
        for col, (i, item) in zip(cols, enumerate(items[start:start + per_row], start)):
            with col:
                product_card(item, source, f"{key}-{i}-{item['product_id']}", allow_feedback)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def page_home(user_id: str) -> None:
    st.title("🛍️ Parvaaz Store")
    profile = safe(api.get, f"/users/{user_id}/profile")
    if profile:
        st.caption(" ".join(profile["summary"]))

    query = st.text_input("Search products", placeholder="e.g. phone, atta, shoes", key="search_box")
    if query:
        if st.session_state.get("last_search") != query:  # log each search once, not on every re-run
            log_event("search", "search", search_query=query)
            st.session_state.last_search = query
        results = safe(api.get, "/products", q=query, limit=10) or []
        st.subheader(f"Results for “{query}”")
        product_row(results, "search", "search")

    st.subheader("✨ Recommended for you")
    product_row(safe(api.get, f"/recommend/{user_id}", k=10) or [], "recommendation", "rec", allow_feedback=True)

    due = safe(api.get, f"/users/{user_id}/buy_again") or []
    if due:
        st.subheader("🔁 Buy again")
        product_row(due, "recommendation", "again")

    st.subheader("🔥 Trending now")
    product_row(safe(api.get, "/trending", k=5) or [], "home", "trend")


def page_product(user_id: str) -> None:
    pid = st.session_state.product_id
    if not pid:
        st.info("Open a product from the Home page first.")
        return
    item = safe(api.get, f"/products/{pid}")
    if not item:
        return
    if st.session_state.get("viewed") != pid:  # one 'view' event per visit
        log_event("view", "product_page", pid)
        st.session_state.viewed = pid

    st.title(item["title"])
    left, right = st.columns([2, 1])
    with left:
        st.caption(f"{item['brand']} · {item['category_name']} · ⭐ {item['avg_rating']} ({item['rating_count']} ratings)")
        st.markdown(price_line(item))
        st.write(item["description"])
        if item.get("age_restricted"):
            st.warning("18+ product")
        if item.get("is_consumable") and item.get("repurchase_days"):
            st.caption(f"People usually re-buy this every {int(item['repurchase_days'])} days.")
    with right:
        st.metric("In stock", int(item["stock_qty"]))
        st.button("Add to cart 🛒", on_click=add_to_cart, args=(pid, "product_page"),
                  use_container_width=True, type="primary")
        st.button("Wishlist ❤️", on_click=wishlist, args=(pid,), use_container_width=True)

    st.subheader("🤝 Customers also bought")
    product_row(safe(api.get, f"/complements/{pid}", k=5) or [], "product_page", "comp")
    st.subheader("🔄 Similar items")
    product_row(safe(api.get, f"/similar/{pid}", k=5) or [], "product_page", "sim")


def page_cart(user_id: str) -> None:
    st.title("🛒 Cart")
    cart = st.session_state.cart
    if not cart:
        st.info("Your cart is empty.")
        return
    products = {pid: safe(api.get, f"/products/{pid}") for pid in cart}
    total = 0.0
    for pid, qty in cart.items():
        p = products[pid]
        if not p:
            continue
        total += p["price"] * qty
        c1, c2, c3 = st.columns([4, 1, 1])
        c1.markdown(f"**{p['title']}** — ₹{p['price']:,.0f} × {qty}")
        c2.markdown(f"₹{p['price'] * qty:,.0f}")
        c3.button("Remove", key=f"rm-{pid}", on_click=remove_from_cart, args=(pid,))
    st.divider()
    st.markdown(f"### Total: ₹{total:,.0f}")
    st.button("Place order", type="primary", on_click=checkout)
    st.caption("Leave without ordering and the system notices: an **abandoned cart** becomes a trigger "
               "for a Telegram offer (see Admin → Triggers).")


def page_controls(user_id: str) -> None:
    st.title("🎛️ My controls")
    profile = safe(api.get, f"/users/{user_id}/profile")
    if not profile:
        return
    prefs = profile["prefs"]

    st.subheader("What we think about you")
    for line in profile["summary"]:
        st.markdown(f"- {line}")

    st.subheader("Tune your recommendations")
    with st.form("controls"):
        use_budget = st.checkbox("Set a budget", value=prefs["budget_max"] is not None)
        budget = st.slider("Max price (₹)", 100, 50000, int(prefs["budget_max"] or 5000), step=100)
        explore = st.slider("Familiar ↔ Explore", 0.0, 1.0, float(prefs["explore"]), step=0.1,
                            help="0 = only what you usually like · 1 = mix in new categories")
        gift = st.toggle("🎁 Gift mode (shopping for someone else)", value=prefs["gift_mode"])
        if st.form_submit_button("Save", type="primary"):
            body = {"explore": explore, "gift_mode": gift}
            body.update({"budget_max": budget} if use_budget else {"clear_budget": True})
            if safe(api.put, f"/users/{user_id}/prefs", body) is not None:
                if gift != prefs["gift_mode"]:
                    new_session(is_gift=gift)  # gift clicks go to a gift session
                flash("Saved — see your updated picks below.")
                st.rerun()

    hidden = prefs["hidden_brands"] + prefs["hidden_categories"]
    if hidden:
        st.caption("Hidden because you said “not interested”: " + ", ".join(hidden))

    st.subheader("Your picks with these settings")
    st.caption("Use “Not interested…” on any card — the reason you pick decides what changes.")
    product_row(safe(api.get, f"/recommend/{user_id}", k=10) or [], "recommendation", "ctl", allow_feedback=True)

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Reset my profile")
        st.caption("Forget what we learned and your settings. You'll start with popular items.")
        if st.button("Reset my profile", type="secondary"):
            res = safe(api.post, f"/users/{user_id}/reset_profile")
            if res:
                flash(res["message"])
                st.rerun()
    with c2:
        st.subheader("Get offers on Telegram")
        tg = profile["telegram"]
        st.caption("✅ Linked to Telegram" if tg["linked"] else "Not linked yet. Opening the link and "
                   "pressing **Start** in Telegram = you agree to receive offers. Send /stop any time.")
        if st.button("Get my Telegram link"):
            res = safe(api.post, f"/users/{user_id}/telegram_link")
            if res:
                st.session_state.tg_link = res["deep_link"]
        link = st.session_state.get("tg_link")
        if link:
            st.markdown(f"[{link}]({link})")
            qr = qr_png(link)
            if qr:
                st.image(qr, width=180)


def qr_png(text: str) -> bytes | None:
    """QR code image for the deep link (scan with your phone). None if `qrcode` isn't installed."""
    try:
        import qrcode
    except ImportError:
        return None
    buf = io.BytesIO()
    qrcode.make(text).save(buf, format="PNG")
    return buf.getvalue()


def page_chat(user_id: str) -> None:
    st.title("💬 Shopping assistant")
    st.caption("Ask for ideas (“gift for my dad under ₹2000”). Prices and products always come from our "
               "catalog, never made up by the AI.")
    for i, turn in enumerate(st.session_state.chat):
        with st.chat_message(turn["role"]):
            st.markdown(turn["text"])
            if turn.get("products"):
                product_row(turn["products"], "recommendation", f"chat{i}", per_row=3)

    suggestions = st.session_state.chat[-1].get("buttons", []) if st.session_state.chat else []
    clicked = None
    if suggestions:
        cols = st.columns(len(suggestions))
        for col, label in zip(cols, suggestions):
            if col.button(label, key=f"sugg-{len(st.session_state.chat)}-{label}"):
                clicked = label
    message = st.chat_input("Type a message") or clicked
    if message:
        st.session_state.chat.append({"role": "user", "text": message})
        reply = safe(api.post, "/chat", {"user_id": user_id, "message": message})
        if reply:
            st.session_state.chat.append({"role": "assistant", "text": reply["text"],
                                          "products": reply["products"], "buttons": reply["buttons"]})
        st.rerun()


def page_admin(user_id: str) -> None:
    st.title("📊 Admin / Insights")
    data = safe(api.get, "/admin/insights")
    if not data:
        return
    c1, c2 = st.columns(2)
    c1.metric("Data level", data["data_level"])
    rt = data["redteam"]
    c2.metric("Red-team score", f"{rt['score_pct']}%",
              help=f"{rt['attacks_blocked']}/{rt['attacks_total']} attacks blocked, "
                   f"{rt['benign_allowed']}/{rt['benign_total']} normal questions allowed")
    st.caption("Modules: " + " · ".join(f"{m} = {s}" for m, s in data["modules"].items()))

    st.subheader("Offer funnel")
    offers = data["offers"]
    # Each stage shows how many of the previous stage made it this far (conversion).
    cols = st.columns(len(offers["funnel"]) + 2)
    previous = None
    for col, stage in zip(cols, offers["funnel"]):
        delta = f"{100 * stage['count'] / previous:.0f}% of previous" if previous else None
        col.metric(stage["stage"].capitalize(), stage["count"], delta=delta, delta_color="off")
        previous = stage["count"] or None
    cols[-2].metric("Blocked by guardrails", offers["blocked"])
    cols[-1].metric("Failed to send", offers["failed"])
    if offers["by_trigger"]:
        st.dataframe(pd.DataFrame(offers["by_trigger"]), hide_index=True, use_container_width=True)

    st.subheader("Sent offers")
    st.caption("Every offer the system tried to send, newest first. "
               "blocked = a guardrail said no, failed = Telegram didn't accept it.")
    f1, f2, f3 = st.columns(3)
    status = f1.selectbox("Status", ["All", "queued", "sent", "opened", "clicked", "redeemed", "blocked", "failed"])
    trigger_filter = f2.selectbox("Trigger", ["All", "cart_abandon", "price_drop", "repurchase_due",
                                              "post_purchase", "win_back", "festival"])
    user_filter = f3.text_input("User ID", placeholder="e.g. U00001 (empty = all)").strip()
    sends = safe(api.get, "/admin/offer_sends",
                 status=None if status == "All" else status,
                 trigger_type=None if trigger_filter == "All" else trigger_filter,
                 user_id=user_filter or None, limit=500)
    if sends:
        st.dataframe(pd.DataFrame(sends), hide_index=True, use_container_width=True,
                     column_config={"message": st.column_config.TextColumn("message", width="large")})
        st.caption(f"Showing {len(sends)} send(s) (max 500).")
    elif sends is not None:
        st.caption("No offer sends match these filters.")

    st.subheader("Guardrail blocks (audit_log)")
    blocks = data["guardrails"]
    if blocks["by_reason"]:
        st.dataframe(pd.DataFrame(blocks["by_reason"]), hide_index=True, use_container_width=True)
        with st.expander("Most recent blocks"):
            st.dataframe(pd.DataFrame(blocks["recent"]), hide_index=True, use_container_width=True)
    else:
        st.caption("No blocks logged yet.")

    st.subheader("Model results")
    if data["model_results"]:
        st.markdown(data["model_results"]["text"])
    else:
        st.caption("reco/results.md doesn't exist yet (Phase 2 writes it after evaluation).")
    if data["redteam_report"]:
        with st.expander(f"Red-team report ({data['redteam_report']['path']})"):
            st.markdown(data["redteam_report"]["text"])

    st.subheader("Triggers & offers (live demo)")
    when = st.date_input("Check triggers as of", value=now_local().date())
    found = safe(api.get, "/admin/triggers", now=f"{when}T23:59:00") or []
    st.dataframe(pd.DataFrame(found or [{"user_id": "—", "trigger_type": "no triggers"}]),
                 hide_index=True, use_container_width=True)
    trigger = st.selectbox("Send an offer to the current user for trigger",
                           ["cart_abandon", "post_purchase", "repurchase_due", "price_drop", "win_back", "festival"])
    if st.button(f"Run offer pipeline for {user_id}", type="primary"):
        res = safe(api.post, "/admin/run_offer", {"user_id": user_id, "trigger_type": trigger})
        if res:
            st.success(f"Pipeline result: **{res['status']}** (blocked = a guardrail said no; see the table above)")


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300, show_spinner=False)
def load_users() -> list[dict]:
    return api.get("/users")


def main() -> None:
    init_state()
    with st.sidebar:
        st.header("Parvaaz Store")
        try:
            health = api.get("/health")
            users = load_users()
        except ApiError as exc:
            st.error(str(exc))
            st.code("uvicorn api.main:app --port 8000", language="bash")
            st.stop()

        labels = {u["user_id"]: f"{u['user_id']} · {u['city']} · {u['age_group']}"
                  + (" · minor" if u["is_minor"] else "") + f" · {u['n_events']} events" for u in users}
        if st.session_state.get("user_id") not in labels:  # first visit: U00001 has the planted demo cases
            st.session_state.user_id = "U00001" if "U00001" in labels else next(iter(labels))
        st.selectbox("Demo user", list(labels), format_func=labels.get, key="user_id", on_change=change_user)
        if st.session_state.session_id is None:
            new_session()
        st.radio("Page", PAGES, key="page")
        n_items = sum(st.session_state.cart.values())
        st.caption(f"🛒 {n_items} item(s) in cart")
        st.divider()
        st.caption("Modules: " + ", ".join(f"{m} = {s}" for m, s in health.get("modules", {}).items()))

    if st.session_state.flash:
        message, icon = st.session_state.flash
        st.toast(message, icon=icon)
        st.session_state.flash = None

    user_id = st.session_state.user_id
    {
        "🏠 Home": page_home, "📦 Product": page_product, "🛒 Cart": page_cart,
        "🎛️ My controls": page_controls, "💬 Chat": page_chat, "📊 Admin / Insights": page_admin,
    }[st.session_state.page](user_id)


main()
