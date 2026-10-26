# How to Run the Phases

Each phase is a **prompt file**. Open a **new chat** with this project folder selected, then paste the whole content of the phase file as your first message.

## Order

```
Wave 1 (alone)      PHASE_0_foundation            ← must finish first (creates shared code + sample data)
                              ↓
Wave 2 (parallel)   PHASE_1_data_generator
                    PHASE_2_recommendation_engine
                    PHASE_3_offer_engine
                    PHASE_4_llm_guardrails
                    PHASE_5_telegram_bot
                    PHASE_6_api_and_ui
                              ↓
Wave 3 (alone)      PHASE_7_integration           ← wires everything together, end-to-end test, report
```

Why this works in parallel: Phase 0 creates the **shared contracts** (`common/schemas.py`), **sample data** (`data/sample/`) and **stubs** (`common/stubs.py`). Every Wave-2 phase codes against those, so nobody waits for anybody.

## Suggested timeline (~8 weeks, adjust to your team)

| Week | Work |
|---|---|
| 1 | Phase 0 + everyone reads `docs/BEGINNER_GUIDE.md` and finishes `docs/SETUP_GUIDE.md` |
| 2–5 | Phases 1–6 in parallel (one person/chat per phase; one person can own 2 small phases) |
| 6 | Phase 2 retrains on full data from Phase 1; fix issues in `phases/NOTES.md` |
| 7 | Phase 7 integration + end-to-end demo |
| 8 | Evaluation report, red-team results, presentation |

## Team split suggestion (4 people)

- **Person A:** Phase 0 → Phase 1 (data)
- **Person B:** Phase 2 (recommendation engine)
- **Person C:** Phase 3 (offers) + Phase 5 (Telegram)
- **Person D:** Phase 4 (LLM + guardrails) + Phase 6 (API + UI)
- **Everyone:** Phase 7

## Tips

- One phase = one chat. If a chat gets long and confused, start a new chat and say: *"Continue PHASE_X. Read CLAUDE.md, phases/PROGRESS.md and <module>/README.md first."*
- Found a problem in another module? Don't fix it there — write it in `phases/NOTES.md`.
- Tick progress in `phases/PROGRESS.md`.
