# Parvaaz — Personalised Offer Engine

**Which offer should each customer get, and when?** A discount that wins back a customer
who is about to leave is worth it. The same discount sent to someone who would buy anyway
is lost profit. Parvaaz decides who gets which offer for an Indian online marketplace and
delivers it on **Telegram: [@Sutlejbot](https://t.me/Sutlejbot)**.

> **Try it:** open [@Sutlejbot](https://t.me/Sutlejbot), press **Start**, and pick a shopper to play.
> You take over that synthetic customer's shopping history and get the offer the system would
> send them, with a "behind the scenes" note on what the AI chose and which rules it passed.

## How a decision is made: "the AI proposes, the rules decide"

```
Something happens            cart left 2 h ago · wishlist item got cheaper · groceries due ·
                             just bought a phone · inactive 30+ days · Diwali sale
      ↓
Rules: may we message them?  opted in · quiet hours · max offers per day / week
Control group?               10% get nothing, so we can measure what offers really add
Would they buy anyway?       premium regulars get a reminder, not a discount
      ↓
Offer Engine builds offers   size = starting % × shopper type, capped, never below the profit floor
Rec engine ranks them        best fit for THIS shopper first
Guardrails                   caps · margin · no stacking · age limits · daily budget · approval
      ↓
Claude CHOOSES               one offer from the rule-approved shortlist, or "no discount"
                             → anything outside the shortlist is rejected and logged
Claude WRITES                the wording only; codes, prices, % and dates are filled in from the DB
Re-check → Send on Telegram  → track opened / clicked / redeemed
```

Every step writes a row to `audit_log`, so every decision can be explained. If Claude fails,
the rec engine's pick and a plain template are used, so the customer still gets a valid offer.

## The 7 demo shoppers (what each one shows)

| In the bot | Reason for the offer | What should happen |
|---|---|---|
| 🎓 Budget student | left items in the cart | a bigger-than-usual discount on the exact item |
| 🛒 Monthly restocker | groceries due again | a small "buy again" discount |
| 📱 New gadget owner | just bought a phone | a bundle on a matching accessory, **never another phone** |
| 😴 Lapsed customer | 30+ days inactive | a "we miss you" discount on their favourite category |
| 💖 Wishlist watcher | wishlisted item got cheaper | a flat ₹ discount on that item |
| 🪔 Diwali gift shopper | festival sale | a festival discount on the categories they buy most |
| 💎 Premium regular | left items in the cart | **no discount**, just a reminder: they'd buy anyway |

`/switch` picks another shopper. Organisers can push a fresh round to every tester with
`python -m pipeline.demo_round` (add `--dry-run` to only preview it).

## For the marketing team: the Offer Control Room

`http://localhost:8502` (started by `run_all`). No code needed:

- **📏 Offer rules**: sliders for every limit (maximum discount, profit floor, offers per week,
  quiet hours, discount per reason, shopper-type multipliers, the "would buy anyway" rule,
  control group size, whether Claude may choose). A live example shows what a change does
  *before* you save. Everything is stored in one commented file, [offers/rules.yaml](offers/rules.yaml),
  which is checked when saved: a typo can never switch a limit off.
- **📈 Results**: our engine vs simpler strategies (extra purchases per ₹ of discount), live
  Telegram results, Claude's choices accepted vs overruled by the rules, and why offers were not sent.
- **🧪 Demo testers**: who is playing which shopper, and what they received.

**Headline result** (simulation on the synthetic store, 1,500 customers): our engine gets **2.6× more
extra purchases** than "biggest discount for everyone" at **half the cost per extra purchase**
(₹110 vs ₹222). Details: [pipeline/offer_eval.md](pipeline/offer_eval.md), [docs/REPORT.md](docs/REPORT.md).

## The synthetic data

10,000 customers and ~1M events (Apr–Sep 2026) with realistic, planted behaviour: hidden taste
profiles, long-tail popularity, purchase funnels (view → cart → buy, with drop-off), repurchase
cycles for consumables, complement/substitute products, seasonal and sale spikes, gift sessions,
cold-start users and items, and noise. The hidden "truth" is kept apart and used only to evaluate.
See [data_gen/README.md](data_gen/README.md) and [docs/DATA_PARAMETERS.md](docs/DATA_PARAMETERS.md).
Real datasets (RetailRocket, Amazon, Myntra) can be loaded through the same format ([docs/DATA_STANDARD.md](docs/DATA_STANDARD.md)).

## Setup (Windows PowerShell)

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1     # 1. virtual environment
pip install -r requirements.txt                         # 2. packages
copy .env.example .env                                  # 3. private settings: add ANTHROPIC_API_KEY,
                                                        #    TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_USERNAME
python scripts/check_setup.py --online                  # 4. health check
powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1   # 5. start everything
```

`run_all` first runs `scripts/preflight.py`: it loads the data, trains the models and prepares the
demo shoppers if needed, then starts the Offers API (:8001), Main API (:8000), Storefront (:8501),
Control Room (:8502), the Telegram bot and the 15-minute offer scheduler. `bash scripts/run_all.sh`
does the same on macOS / Linux / Git Bash. Keys stay in `.env` (never committed); without a Claude
key the system runs in mock mode, and without a Telegram token it runs without sending.

Tests: `pytest` (448 tests, ~5 min) · Keys and troubleshooting: [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md) ·
Beginners: [docs/BEGINNER_GUIDE.md](docs/BEGINNER_GUIDE.md) · Project rules: [CLAUDE.md](CLAUDE.md)

## What's in the repo

| Folder | What it does |
|---|---|
| `data_gen/`, `ingest/` | synthetic data generator · adapters for real datasets |
| `reco/` | recommendations: popularity, item-CF, ALS, content → LightGBM ranker → re-ranking |
| `offers/` | Offer Engine, Offers API (key-protected), eligibility, guardrails, **rules.yaml** |
| `llm/` | Claude: offer **chooser**, offer **writer**, shopping chat agent, input/output guardrails |
| `bot/` | Telegram bot, demo shoppers (`personas.py`), send queue, buttons → events |
| `pipeline/` | triggers, the offer pipeline, scheduler, evaluation, `demo_round` |
| `api/`, `ui/` | Main API · Storefront (8501) · **Offer Control Room (8502)** |
