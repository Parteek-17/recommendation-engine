# Setup & Connection Guide

Simple, step-by-step. Commands are for **Windows PowerShell** (Mac/Linux alternative shown where different).
Run each step in order; each ends with a ✅ check.

---

## Step 1 — Install Python 3.11
1. Download from **python.org/downloads** (3.11.x).
2. In the installer, **tick "Add python.exe to PATH"**, then Install.
3. ✅ Open a **new** PowerShell and run:
   ```powershell
   python --version      # should print Python 3.11.x
   ```

## Step 2 — Install editor & tools
- **VS Code** (code.visualstudio.com) + the **Python** extension.
- **Git** (git-scm.com) — optional but recommended.
- **DB Browser for SQLite** (sqlitebrowser.org) — optional, to look inside the database.

## Step 3 — Open the project & create a virtual environment
A virtual environment ("venv") keeps this project's packages separate from everything else.
```powershell
cd "C:\Users\<you>\Documents\college\Parvaaz\ecommerce recommendation engine"
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Mac/Linux: source .venv/bin/activate
```
✅ Your prompt now starts with `(.venv)`. **Activate it every time** you open a new terminal.

## Step 4 — Install packages
(Available after Phase 0 has created `requirements.txt`.)
```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```
✅ No red `ERROR` lines at the end.

## Step 5 — Create your `.env` file (your private settings)
```powershell
copy .env.example .env                 # Mac/Linux: cp .env.example .env
```
Open `.env` in VS Code and fill values in the next steps. **Rules:**
- `.env` is never shared, never committed to git, never pasted in chat.
- Write values without quotes or spaces: `ANTHROPIC_API_KEY=sk-ant-...`

## Step 6 — Claude API key (for the AI parts)
1. Go to **console.anthropic.com** → sign up / log in.
2. **Billing** → add a small amount of credit (the project uses very little).
3. **API Keys** → **Create Key** → name it `parvaaz-dev` → copy it (shown only once).
4. In `.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-xxxxxxxx
   CLAUDE_MODEL_FAST=claude-haiku-5-5
   CLAUDE_MODEL_SMART=claude-sonnet-5-5
   ```
   Model names change over time — check the current list at **docs.claude.com → Models** and update if needed.
5. Optional safety: in the console set a **monthly spend limit**.

✅ `python scripts/check_setup.py --online` shows ✅ for Anthropic.
No key yet? Leave it empty — the LLM module runs in **mock mode** so you can still develop.

## Step 7 — Telegram bot token (for sending offers)
1. In Telegram, open **@BotFather** (blue tick) → send `/newbot`.
2. Give a display name (e.g. `Parvaaz Shop`) and a username ending in `bot` (e.g. `parvaaz_shop_bot`).
3. BotFather replies with a **token** like `123456789:AAH...`. Copy it.
4. In `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456789:AAH...
   TELEGRAM_BOT_USERNAME=parvaaz_shop_bot
   ```
5. Optional: send BotFather `/setcommands`, choose your bot, paste:
   ```
   start - Link your account
   offers - My current offers
   recommend - Picks for me
   settings - Quiet hours and preferences
   gift - Toggle gift mode
   stop - Stop all offers
   help - How to use
   ```
6. Open your bot in Telegram and press **Start** (a bot can only message people who started it).

✅ `python scripts/check_setup.py --online` shows ✅ for Telegram.

## Step 8 — Offers API key (our own internal password)
Create a random secret:
```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
Paste into `.env` as `OFFERS_API_KEY=...`. Only our backend uses it to call our Offers API.

## Step 9 — Other settings in `.env`
```
DB_PATH=data/app.db
DATA_SOURCE=sample              # sample → synthetic (after Phase 1) → external (real datasets, see docs/DATA_STANDARD.md)
EXTERNAL_SOURCE=                # e.g. amazon_reviews_2023 when DATA_SOURCE=external
OFFERS_API_URL=http://127.0.0.1:8001
MAIN_API_URL=http://127.0.0.1:8000
TIMEZONE=Asia/Kolkata           # used for quiet hours
```
Guardrail limits (`MAX_DISCOUNT_PCT`, `MAX_OFFERS_PER_DAY`, `QUIET_START`, …) have safe defaults in `.env.example`.

## Step 10 — Create the database & run the health check
```powershell
python scripts/init_db.py
python scripts/check_setup.py           # add --online to also test Claude + Telegram
```
✅ All lines ✅. Fix any ❌ using Troubleshooting below.

## Step 11 — Running the system
Each service in its **own terminal** (venv activated in each):

| What | Command | Open |
|---|---|---|
| Offers API | `uvicorn offers.api:app --port 8001` | http://127.0.0.1:8001/docs |
| Main API | `uvicorn api.main:app --port 8000` | http://127.0.0.1:8000/docs |
| Telegram bot | `python -m bot.main` | your bot in Telegram |
| Offer scheduler | `python -m pipeline.scheduler --dry-run` | prints what it would send |
| Storefront UI | `streamlit run ui/app.py` | http://localhost:8501 |

After Phase 7: `.\scripts\run_all.ps1` starts everything at once. Stop any service with **Ctrl + C**.

---

## Security checklist (read once, follow always)
- [ ] `.env` is listed in `.gitignore` and not committed (`git status` must not show it)
- [ ] Never paste keys into chats, screenshots, code, or Telegram
- [ ] Leaked a key? **Revoke it immediately** (Anthropic console → API Keys; BotFather → `/revoke`) and create a new one
- [ ] Each teammate uses their **own** Telegram test bot during development (two copies of the same bot can't run at once)
- [ ] Keep a spend limit on the Anthropic account

---

## Troubleshooting

| Problem / error message | Fix |
|---|---|
| `python is not recognized` | Reinstall Python with **"Add to PATH"** ticked, then open a new terminal. Or try `py` instead of `python`. |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | Run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` → type `Y`. |
| `ModuleNotFoundError: No module named ...` | venv not active (no `(.venv)` in prompt) → activate it; or run `pip install -r requirements.txt` again. |
| `pip install` fails on `implicit` or `lightgbm` | Install **Microsoft C++ Build Tools** (visualstudio.microsoft.com/visual-cpp-build-tools) or use Python 3.11 (has ready wheels). Phase 2 also has a fallback without `implicit`. |
| Settings/keys not loading | Windows may have saved the file as `.env.txt`. In VS Code rename it to exactly `.env`. Must be in the project root folder. |
| Anthropic `401 authentication_error` | Key wrong / revoked / has spaces or quotes. Create a new key. |
| Anthropic `credit balance is too low` | Add credit in console → Billing. |
| Anthropic `429 rate_limit_error` / `529 overloaded` | Wait a little; code retries automatically. Reduce test loops. |
| Anthropic `model not found` | Update `CLAUDE_MODEL_FAST` / `CLAUDE_MODEL_SMART` from docs.claude.com → Models. |
| Telegram `Unauthorized` / `InvalidToken` | Token copied wrong — copy again from BotFather (`/mybots` → API Token). |
| Telegram `Conflict: terminated by other getUpdates request` | The same bot is running twice (another terminal or teammate). Stop the other one. |
| Bot doesn't message a user | That user never pressed **Start**, pressed `/stop`, or blocked the bot. Also check quiet hours. |
| Offers API returns `401` | `X-API-Key` header missing or `OFFERS_API_KEY` differs between services — restart services after editing `.env`. |
| `[Errno 10048] address already in use` / port busy | Service already running elsewhere. Close it, or find it: `netstat -ano | findstr :8000` → `taskkill /PID <pid> /F`. |
| `database is locked` | Close DB Browser for SQLite (or other programs using `app.db`), then retry. |
| Streamlit page doesn't open | Open http://localhost:8501 manually; allow Python through Windows Firewall if asked. |
| Quiet hours wrong | Check `TIMEZONE=Asia/Kolkata` in `.env`. |
| Everything is confusing | Run `python scripts/check_setup.py`, copy the output (it hides keys) and ask in your phase chat. |
