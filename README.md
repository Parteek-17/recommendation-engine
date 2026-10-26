# E-commerce Recommendation Engine (Parvaaz)

Personalised recommendations + smart offers for an Indian marketplace, delivered via Telegram, with guardrailed Claude AI.
New here? Read [docs/BEGINNER_GUIDE.md](docs/BEGINNER_GUIDE.md). Project rules and contracts: [CLAUDE.md](CLAUDE.md).

## Quick start (Windows PowerShell)

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1     # 1. create + activate a virtual environment
pip install -r requirements.txt                         # 2. install packages
copy .env.example .env                                  # 3. create your private settings (fill keys later)
python scripts/init_db.py                               # 4. create data/app.db and load sample data
python scripts/check_setup.py                           # 5. health check (add --online to test Claude + Telegram)
pytest tests/common                                     # 6. run the foundation tests
```

## Run the whole system (one command)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1            # Windows: one window per service
powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1 -DryRun    # scheduler prints, never sends
```

```bash
bash scripts/run_all.sh            # macOS / Linux / Git Bash (logs in logs/, Ctrl+C stops all)
```

It first runs `scripts/preflight.py` (creates the internal `OFFERS_API_KEY` if missing, loads data and trains
the models if needed), then starts the Offers API (:8001), Main API (:8000), Storefront (:8501), the Telegram bot
(if `TELEGRAM_BOT_TOKEN` is set) and the offer scheduler, and prints what's running where.
Demo click-path: [ui/DEMO.md](ui/DEMO.md). How the offer flow is wired: [pipeline/README.md](pipeline/README.md).
Results: [docs/REPORT.md](docs/REPORT.md).

Step-by-step help, API keys and troubleshooting: [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md).
Data format: [docs/DATA_STANDARD.md](docs/DATA_STANDARD.md) · Data knobs: [docs/DATA_PARAMETERS.md](docs/DATA_PARAMETERS.md) · Phases: [phases/README.md](phases/README.md) · Shared code: [common/README.md](common/README.md)
