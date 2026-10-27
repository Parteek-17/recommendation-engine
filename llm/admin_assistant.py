"""Admin assistant: a chatbot for the store's marketing/admin team (Admin dashboard, 🤖 Assistant).

    reply = ask("How did offers do this week?", history=[])
    reply.text      # the answer, written by Claude from tool results (numbers come from the DB)
    reply.draft     # an OfferDraft if the admin asked for an offer, else None

It answers daily questions (sales, offers, approvals, blocked offers, best sellers, the rules)
and drafts offers. Same idea as the rest of the system — "LLM proposes, rules decide":
- every tool is READ-ONLY; the one "write-like" tool, draft_offer, only builds a draft and
  checks it with offers.manual (the same rules as everything else)
- saving a draft needs the admin to press "Create offer" on the dashboard
- no tool can read secrets, change the rules file, approve offers or message customers
Without an API key (mock mode) a simple keyword router answers with the same tools.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from common.db import get_connection
from common.logging_utils import audit, get_logger
from llm.client import LLMError, get_client, text_of

log = get_logger("llm.admin")

MAX_TOOL_CALLS = 6
TURN_TIMEOUT_S = 60
MAX_INPUT_CHARS = 2000
LIVE_FROM = "2026-10-01"  # the synthetic store history ends on 30 Sep 2026
ROOT = Path(__file__).resolve().parent.parent

ADMIN_SYSTEM_PROMPT = """You are the admin assistant of "Ecommerce Store", an Indian online marketplace \
(prices in ₹). You help the marketing and admin team with daily questions and with making offers.

How to work:
- Use the tools for EVERY number, product, category, offer or rule you mention. Never invent figures.
- Store history is synthetic data from 1 Apr to 30 Sep 2026; anything sent from 1 Oct is live.
  Say which one a number comes from when it matters.
- To make an offer, call draft_offer, but ONLY when the admin's latest message asks for a new or \
changed offer (never re-draft an earlier one on your own). You can only DRAFT: tell the admin to \
check it and press "Create offer" below your answer to save it. The coupon code is shown on the \
preview card; don't write it yourself. If the draft has problems, explain them simply and \
suggest a fix that would pass (for example a smaller discount).
- Use find_categories / find_products to turn names into IDs before drafting.
- You cannot approve offers, change the rules, message customers or see API keys. If asked, say \
where in the dashboard a person can do it (Offers page, Offer rules page).
- Customer groups allowed for offers: all, new_users, returning, tier1, tier2, tier3, \
price_sensitive, premium. Never target by gender, religion, caste, health or similar.
- Be short and practical: a few sentences or a small markdown table. Plain English, no jargon."""

_SECRET_WORDS = re.compile(r"api[_ ]?key|token|\.env|password|secret", re.I)


@dataclass
class AdminReply:
    text: str
    draft: object | None = None              # offers.manual.OfferDraft
    tools_used: list[str] = field(default_factory=list)
    mode: str = "ai"                         # "ai" | "mock" | "fallback"


# ---------------------------------------------------------------------------
# Tools (read-only)
# ---------------------------------------------------------------------------


def _rows(sql: str, params=()) -> list[dict]:
    with get_connection() as conn:
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        except Exception:  # optional table not created yet
            return []


def _one(sql: str, params=()):
    rows = _rows(sql, params)
    return next(iter(rows[0].values())) if rows else None


def store_overview() -> dict:
    """Headline numbers for a daily check-in."""
    last = _one("SELECT MAX(order_date) FROM orders") or ""
    week = _rows("SELECT COUNT(*) AS orders, ROUND(SUM(total_amount)) AS revenue FROM orders "
                 "WHERE order_date >= date(?, '-6 days')", (last[:10],))[0] if last else {}
    live_sends = _rows("SELECT status, COUNT(*) AS n FROM offer_sends WHERE sent_at >= ? GROUP BY status",
                       (LIVE_FROM,))
    return {
        "customers": _one("SELECT COUNT(*) FROM users"),
        "products": _one("SELECT COUNT(*) FROM products"),
        "products_out_of_stock": _one("SELECT COUNT(*) FROM products WHERE stock_qty <= 0"),
        "last_order_date_in_data": last[:10],
        "orders_last_7_days_of_data": week.get("orders"),
        "revenue_last_7_days_of_data_inr": week.get("revenue"),
        "live_offers": _one("SELECT COUNT(*) FROM offers WHERE is_active = 1 AND expires_at >= datetime('now') "
                            "AND requires_approval = 0"),
        "offers_waiting_for_approval": _one("SELECT COUNT(*) FROM offers WHERE is_active = 1 AND requires_approval = 1"),
        "live_telegram_sends_by_status": {r["status"]: r["n"] for r in live_sends},
        "demo_testers": _one("SELECT COUNT(*) FROM tester_personas") or 0,
        "customers_opted_in": _one("SELECT COUNT(*) FROM user_consent WHERE opted_in = 1"),
    }


def offer_results() -> dict:
    """Are offers working? Simulation result + delivered/used counts, live and history."""
    out: dict = {}
    eval_file = ROOT / "pipeline" / "offer_eval.json"
    if eval_file.exists():
        ev = json.loads(eval_file.read_text(encoding="utf-8"))
        out["simulation"] = {k: {"extra_purchases_per_1000": v["incremental_per_1000"],
                                 "inr_per_extra_purchase": v["cost_per_incremental_inr"]}
                             for k, v in ev["strategies"].items()}
        out["simulation_note"] = "engine = our system; max_discount = biggest discount for everyone"
    for name, cond in (("live_since_1_oct", "sent_at >= ?"), ("synthetic_history", "sent_at < ?")):
        rows = _rows(f"SELECT trigger_type, status, COUNT(*) AS n FROM offer_sends WHERE {cond} "
                     "GROUP BY trigger_type, status", (LIVE_FROM,))
        by: dict = {}
        for r in rows:
            t = by.setdefault(r["trigger_type"] or "other", {"delivered": 0, "used": 0})
            if r["status"] in ("sent", "opened", "clicked", "redeemed"):
                t["delivered"] += r["n"]
            if r["status"] == "redeemed":
                t["used"] += r["n"]
        out[name] = by
    ai = _rows("SELECT decision, COUNT(*) AS n FROM audit_log WHERE action = 'choose_offer' GROUP BY decision")
    out["claude_choices"] = {r["decision"]: r["n"] for r in ai}
    return out


def list_offers(status: str = "live", limit: int = 10) -> dict:
    """Offers by status: live, pending (waiting for approval), manual (made by the admin team) or all."""
    from offers import manual, store

    where = {
        "live": "is_active = 1 AND requires_approval = 0 AND expires_at >= datetime('now')",
        "pending": "is_active = 1 AND requires_approval = 1",
        "manual": "offer_id IN (SELECT json_extract(details_json, '$.offer_id') FROM audit_log "
                  "WHERE action = 'manual_offer')",
        "all": "1 = 1",
    }.get(status, "is_active = 1 AND requires_approval = 0 AND expires_at >= datetime('now')")
    rows = _rows(f"SELECT * FROM offers WHERE {where} ORDER BY offer_id DESC LIMIT ?", (min(int(limit), 25),))
    offers = [store.offer_from_row(r) for r in rows]
    used = {r["offer_id"]: r for r in _rows(
        "SELECT offer_id, COUNT(*) AS sent, SUM(status = 'redeemed') AS used FROM offer_sends GROUP BY offer_id")}
    return {"status": status, "offers": [
        {"offer_id": o.offer_id, "code": o.code, "what": manual.describe(o),
         "expires": o.expires_at.date().isoformat(), "active": o.is_active,
         "waiting_for_approval": o.requires_approval,
         "sent": (used.get(o.offer_id) or {}).get("sent", 0), "used": (used.get(o.offer_id) or {}).get("used", 0)}
        for o in offers]}


def top_products(category: str | None = None, limit: int = 5) -> dict:
    """Best sellers (by units ordered), optionally inside one category (name or ID, sub-categories included)."""
    params: list = []
    cat_sql = ""
    if category:
        cats = _rows("SELECT category_id FROM categories WHERE category_id = ? OR name LIKE ? "
                     "OR parent_id IN (SELECT category_id FROM categories WHERE category_id = ? OR name LIKE ?)",
                     (category, f"%{category}%", category, f"%{category}%"))
        if not cats:
            return {"error": f"no category matches '{category}'"}
        ids = [c["category_id"] for c in cats]
        cat_sql = f"AND p.category_id IN ({','.join('?' * len(ids))})"
        params += ids
    rows = _rows(
        "SELECT p.product_id, p.title, p.price, p.stock_qty, SUM(oi.quantity) AS units, "
        "ROUND(SUM(oi.quantity * oi.price_paid)) AS revenue_inr FROM order_items oi "
        f"JOIN products p ON p.product_id = oi.product_id WHERE 1 = 1 {cat_sql} "
        "GROUP BY p.product_id ORDER BY units DESC LIMIT ?", (*params, min(int(limit), 20)))
    return {"category": category or "all", "products": rows, "period": "Apr–Sep 2026 (synthetic history)"}


def find_categories(query: str = "") -> dict:
    """Categories (with IDs) whose name contains the query; empty query = all."""
    rows = _rows("SELECT c.category_id, c.name, p.name AS parent FROM categories c "
                 "LEFT JOIN categories p ON p.category_id = c.parent_id WHERE c.name LIKE ? ORDER BY c.category_id",
                 (f"%{query}%",))
    return {"categories": rows[:40]}


def find_products(query: str = "", limit: int = 8) -> dict:
    """Products whose title or brand contains the query, with price, profit margin and stock."""
    rows = _rows("SELECT product_id, title, brand, price, margin_pct, stock_qty FROM products "
                 "WHERE title LIKE ? OR brand LIKE ? ORDER BY rating_count DESC LIMIT ?",
                 (f"%{query}%", f"%{query}%", min(int(limit), 20)))
    return {"products": rows}


def current_rules() -> dict:
    """The offer rules marketing set on the Offer rules page (offers/rules.yaml)."""
    from offers import rules

    data = rules.load_rules()
    return {k: data[k] for k in ("limits", "frequency", "starting_discount_pct", "control_group_pct",
                                 "skip_likely_buyers", "ai_choice") if k in data}


def why_blocked(limit: int = 8) -> dict:
    """The most common reasons offers were NOT sent (each one is a rule doing its job)."""
    rows = _rows("SELECT reason FROM audit_log WHERE (action = 'offer_guardrail' AND decision = 'blocked') "
                 "OR (component = 'pipeline' AND action = 'step_eligibility' AND decision = 'blocked')")
    counts: dict[str, int] = {}
    for r in rows:
        key = re.sub(r"\(.*\)", "", re.sub(r"OF\d+", "another offer", r["reason"] or "")).strip()
        counts[key] = counts.get(key, 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    return {"reasons": [{"reason": k, "times": v} for k, v in top]}


TOOLS: dict = {
    "store_overview": (store_overview, "Headline numbers: customers, orders and revenue in the last 7 days "
                       "of data, live offers, approvals waiting, live Telegram sends, testers.", {}),
    "offer_results": (offer_results, "Are offers working: simulation vs simpler strategies, offers delivered "
                      "and used per reason (live and history), Claude choices accepted/overruled.", {}),
    "list_offers": (list_offers, "List offers with how often they were sent and used.",
                    {"status": {"type": "string", "enum": ["live", "pending", "manual", "all"]},
                     "limit": {"type": "integer", "minimum": 1, "maximum": 25}}),
    "top_products": (top_products, "Best-selling products, optionally in one category (name or ID).",
                     {"category": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}),
    "find_categories": (find_categories, "Find categories and their IDs by name.", {"query": {"type": "string"}}),
    "find_products": (find_products, "Find products by title or brand; shows price, margin and stock.",
                      {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}),
    "current_rules": (current_rules, "The current offer rules (limits, frequency, discounts, control group).", {}),
    "why_blocked": (why_blocked, "Most common reasons offers were not sent.",
                    {"limit": {"type": "integer", "minimum": 1, "maximum": 20}}),
}

DRAFT_TOOL = {
    "name": "draft_offer",
    "description": ("Build an offer DRAFT and check it against the store's rules. Does not save it: the admin "
                    "must press 'Create offer'. Use category_id or product_id from find_categories/find_products; "
                    "leave both empty for a whole-cart offer."),
    "input_schema": {"type": "object", "properties": {
        "offer_type": {"type": "string", "enum": ["pct_off", "flat_off", "free_delivery", "bundle", "buy_again", "points"]},
        "value": {"type": "number", "description": "% for pct_off/bundle/buy_again, ₹ for flat_off, points for points"},
        "category_id": {"type": "string"}, "product_id": {"type": "string"}, "bundle_product_id": {"type": "string"},
        "segment": {"type": "string", "enum": ["all", "new_users", "returning", "tier1", "tier2", "tier3",
                                               "price_sensitive", "premium"]},
        "min_cart_value": {"type": "number"}, "valid_days": {"type": "integer", "minimum": 1, "maximum": 60},
        "trigger_type": {"type": "string", "enum": ["cart_abandon", "price_drop", "repurchase_due",
                                                    "post_purchase", "win_back", "festival"]},
    }, "required": ["offer_type"], "additionalProperties": False},
}


def tool_schemas() -> list[dict]:
    schemas = [{"name": name, "description": desc,
                "input_schema": {"type": "object", "properties": props, "additionalProperties": False}}
               for name, (_, desc, props) in TOOLS.items()]
    return schemas + [DRAFT_TOOL]


def run_tool(name: str, args: dict, state: dict) -> tuple[dict, bool]:
    """Run one allowlisted tool. Returns (result, is_error). Bad input -> error text, never a crash."""
    try:
        if name == "draft_offer":
            from offers import manual

            draft = manual.build_offer(**args)
            state["draft"] = draft
            # No coupon code here: it is shown on the preview card (Claude repeating it adds nothing).
            return ({"allowed": draft.ok, "summary": draft.summary() if draft.offer else None,
                     "value_inr_per_use": draft.value_inr, "needs_manager_approval": draft.needs_approval,
                     "problems": draft.problems, "warnings": draft.warnings, "saved": False}, False)
        if name not in TOOLS:
            return {"error": f"unknown tool {name}"}, True
        return TOOLS[name][0](**args), False
    except TypeError as exc:
        return {"error": f"bad arguments for {name}: {exc}"}, True
    except Exception as exc:  # a tool failing must not break the chat
        log.warning("admin tool %s failed: %s", name, exc)
        return {"error": f"{name} failed ({type(exc).__name__})"}, True


# ---------------------------------------------------------------------------
# One turn
# ---------------------------------------------------------------------------


def ask(question: str, history: list[dict] | None = None) -> AdminReply:
    """Answer one admin message. Never raises."""
    question = (question or "").strip()[:MAX_INPUT_CHARS]
    if not question:
        return AdminReply("Ask me anything about the store, offers or rules.", mode="fallback")
    if _SECRET_WORDS.search(question):
        return AdminReply("I can't show or change keys, tokens or passwords. They live only in the server's "
                          "`.env` file, which only the person running the store can open.", mode="fallback")
    state: dict = {"draft": None, "tools": []}
    client = get_client()
    if client.is_mock:
        return _mock_answer(question, state)
    try:
        text = _run(question, history or [], state)
        mode = "ai"
    except LLMError as exc:
        log.warning("admin assistant fell back: %s", exc)
        reply = _mock_answer(question, state)
        reply.text = "_(Claude is unavailable, so here is the plain data.)_\n\n" + reply.text
        reply.mode = "fallback"
        return reply
    draft = state["draft"]
    audit("llm", None, "admin_assistant", "answered", question[:200],
          {"tools": state["tools"], "draft_ok": bool(draft and draft.ok)})
    return AdminReply(text, draft, state["tools"], mode)


def _history_message(m: dict) -> dict:
    """Earlier turns are sent as text only, so say which tools backed an earlier answer.
    Without this note Claude sees facts with no tool call and 'corrects' answers that were right."""
    content = m["content"]
    if m["role"] == "assistant" and m.get("tools"):
        content += f"\n\n[This answer was based on these tool results: {', '.join(m['tools'])}]"
    return {"role": m["role"], "content": content}


def _run(question: str, history: list[dict], state: dict) -> str:
    client = get_client()
    messages: list = [_history_message(m) for m in history[-8:]
                      if m.get("role") in ("user", "assistant") and m.get("content")]
    messages.append({"role": "user", "content": question})
    started, calls = time.monotonic(), 0
    while True:
        if time.monotonic() - started > TURN_TIMEOUT_S:
            raise LLMError("turn timed out")
        response = client.create("smart", system=ADMIN_SYSTEM_PROMPT, messages=messages, tools=tool_schemas(),
                                 max_tokens=2048, effort="low", allow_tools=calls < MAX_TOOL_CALLS)
        uses = [b for b in response.content if getattr(b, "type", "") == "tool_use"]
        if response.stop_reason != "tool_use" or not uses:
            return text_of(response).strip() or "I couldn't find an answer to that."
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in uses:
            calls += 1
            state["tools"].append(block.name)
            output, is_error = ((({"error": "tool limit reached; answer with what you have"}, True))
                                if calls > MAX_TOOL_CALLS else run_tool(block.name, dict(block.input or {}), state))
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": json.dumps(output, ensure_ascii=False, default=str), "is_error": is_error})
        messages.append({"role": "user", "content": results})


# ---------------------------------------------------------------------------
# Mock mode / fallback: keyword router over the same tools
# ---------------------------------------------------------------------------


def _mock_answer(question: str, state: dict) -> AdminReply:
    q = question.lower()

    def use(name: str, **args) -> dict:
        state["tools"].append(name)
        return run_tool(name, args, state)[0]

    if re.search(r"\b(make|create|draft|new)\b.*\boffer\b|\boffer\b.*\b(for|on)\b", q):
        pct = re.search(r"(\d+)\s*%", q)
        flat = re.search(r"(?:₹|rs\.?\s*)(\d+)", q)
        days = re.search(r"(\d+)\s*days?", q)
        seg = next((s for s in ("tier1", "tier2", "tier3", "new_users", "returning", "premium", "price_sensitive")
                    if s.replace("_", " ") in q or s in q), "all")
        cat_id = None
        for c in find_categories()["categories"]:
            if c["name"].lower() in q:
                cat_id = c["category_id"]
        args = {"offer_type": "flat_off" if flat and not pct else "pct_off",
                "value": float((flat or pct).group(1)) if (flat or pct) else 10, "segment": seg,
                "valid_days": int(days.group(1)) if days else 7}
        if cat_id:
            args["category_id"] = cat_id
        if args["offer_type"] == "flat_off":
            args["min_cart_value"] = args["value"] * 4
        r = use("draft_offer", **args)
        if r.get("allowed"):
            text = f"Draft ready: **{r['summary']}**. Check it and press **Create offer** to save."
        else:
            text = "That offer breaks the rules:\n" + "\n".join(f"- {p}" for p in r.get("problems", []))
        if r.get("warnings"):
            text += "\n\nGood to know:\n" + "\n".join(f"- {w}" for w in r["warnings"])
        return AdminReply(text, state["draft"], state["tools"], "mock")

    if re.search(r"approv|pending|waiting", q):
        offers = use("list_offers", status="pending")["offers"]
        text = (f"{len(offers)} offer(s) wait for approval (Offers page):\n" +
                "\n".join(f"- {o['offer_id']} · {o['what']}" for o in offers)) if offers else "No offers wait for approval."
    elif re.search(r"block|not sent|why", q):
        rs = use("why_blocked")["reasons"]
        text = "Most common reasons offers were not sent:\n" + "\n".join(f"- {r['reason']}: {r['times']}" for r in rs)
    elif re.search(r"best|top|sell", q):
        ps = use("top_products")["products"]
        text = "Best sellers (Apr–Sep):\n" + "\n".join(f"- {p['title']}: {p['units']} sold" for p in ps)
    elif re.search(r"rule|limit|max", q):
        r = use("current_rules")
        text = (f"Max discount {r['limits']['max_discount_pct']}%, profit floor "
                f"{r['limits']['min_margin_after_discount_pct']}%, {r['frequency']['max_offers_per_week']} offers "
                f"per customer per week, control group {r['control_group_pct']}%. Change them on the Offer rules page.")
    elif re.search(r"result|work|redeem|perform", q):
        sim = use("offer_results").get("simulation", {})
        e, m = sim.get("engine", {}), sim.get("max_discount", {})
        text = (f"In simulation our engine gets {e.get('extra_purchases_per_1000', '–')} extra purchases per 1,000 "
                f"customers at ₹{e.get('inr_per_extra_purchase', '–')} each, vs "
                f"{m.get('extra_purchases_per_1000', '–')} at ₹{m.get('inr_per_extra_purchase', '–')} for "
                "'biggest discount for everyone'. See the Results page for more.")
    else:
        o = use("store_overview")
        text = (f"**Store today:** {o['customers']:,} customers, {o['products']:,} products "
                f"({o['products_out_of_stock']} out of stock). Last 7 days of data: {o['orders_last_7_days_of_data']} "
                f"orders, ₹{(o['revenue_last_7_days_of_data_inr'] or 0):,.0f}. {o['live_offers']} live offers, "
                f"{o['offers_waiting_for_approval']} waiting for approval, {o['demo_testers']} demo testers.")
    return AdminReply(text, state["draft"], state["tools"], "mock")
