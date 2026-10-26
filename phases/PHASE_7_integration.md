# PHASE 7 — Integration, Offer Pipeline & Evaluation (run LAST)

> Paste everything below into a new chat with the project folder selected. Phases 0–6 should be done (check `phases/PROGRESS.md`).

---

You are building **Phase 7 (Integration)** of the E-commerce Recommendation Engine.

**Before anything:** read `CLAUDE.md`, `phases/PROGRESS.md`, `phases/NOTES.md`, and every module's `README.md`. Resolve open items in `NOTES.md` first. I am a beginner — explain how the pieces connect.

## Goal
Wire every module into the approved **Offer Pipeline**, replace stubs with real modules, run end-to-end, and produce the final evaluation.

## You own
`pipeline/`, `tests/integration/`, `scripts/run_all.*`, `docs/REPORT.md`. You may now fix small integration bugs in any module (note each fix in `NOTES.md`).

## Build
1. **Switch from stubs to real modules**: `DATA_SOURCE=synthetic`, load full data from Phase 1 (also smoke-test once with `DATA_SOURCE=external` on a small real dataset), retrain reco (`python -m reco.train`), make sure nothing still imports `common.stubs` in production paths.
2. **Trigger detection** (`pipeline/triggers.py`): `detect_triggers(now)` for all 6 trigger types (cart idle > 2 h, wishlist price dropped, consumable at `repurchase_days`, purchase in last 3 days with known complements, inactive 30 days, festival window).
3. **Orchestrator** (`pipeline/offer_pipeline.py`): `run_offer_pipeline(user_id, trigger_type)` exactly in the approved order:
   eligibility (`offers.check_eligibility`) → `offers.fetch_live_offers` (Offers API, key server-side) → `reco.rank_offers_for_user` → `offers.apply_offer_guardrails` → `llm.write_offer_message` → `offers.revalidate` → `bot.send_offer` → write `offer_sends`.
   Each step writes `audit_log`; any step fails → stop safely with reason (LLM failure → template message, not stop).
4. **Scheduler** (`pipeline/scheduler.py`): APScheduler every 15 min; `--dry-run` mode prints what *would* be sent without sending.
5. **Run everything**: `scripts/run_all.ps1` (Windows) + `scripts/run_all.sh` start Offers API (8001), Main API (8000), bot, scheduler, UI — each in its own window/process, with a clear "what's running where" printout.
6. **End-to-end tests** (`tests/integration/`): new user links Telegram → abandons cart → pipeline sends offer → click "Apply offer" → status redeemed; opted-out user gets nothing; quiet hours respected; Offers API down → no send + audit entry; LLM down → template message sent.
7. **Evaluation report** (`docs/REPORT.md`): dataset stats; reco results table (each stage vs. baseline, ground-truth recovery); post-purchase & cold-start examples; offer strategies compared (random vs. engine-ranked; redemption rate vs. discount cost); guardrail stats + red-team block rate; architecture diagram; limitations & future work.

## Done when
- [ ] One command starts the full system; demo in `ui/DEMO.md` works end-to-end with real Telegram
- [ ] Integration tests pass; dry-run and live modes both work
- [ ] `docs/REPORT.md` complete with real numbers
- [ ] Phase 7 ticked in `phases/PROGRESS.md`
