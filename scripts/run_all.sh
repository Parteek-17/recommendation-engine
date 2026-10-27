#!/usr/bin/env bash
# Start the whole system (macOS / Linux / Git Bash). Every service runs in the background,
# its output goes to logs/<name>.log, and Ctrl+C stops them all.
#
#   bash scripts/run_all.sh              # live
#   bash scripts/run_all.sh --dry-run    # scheduler only prints, never sends
#   bash scripts/run_all.sh --no-bot --no-scheduler
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DRY_RUN=0; NO_BOT=0; NO_SCHEDULER=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --no-bot) NO_BOT=1 ;;
    --no-scheduler) NO_SCHEDULER=1 ;;
    *) echo "Unknown option: $arg"; exit 2 ;;
  esac
done

# Use the project's virtual environment if there is one.
if [ -x .venv/bin/python ]; then PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
else PY=python3; fi

# 1. Pre-flight: .env, OFFERS_API_KEY, data, trained models.
"$PY" scripts/preflight.py || { echo "Fix the items marked [!!] above, then run this again."; exit 1; }
HAS_BOT=$("$PY" -c "from common.config import settings; print(int(settings.has('TELEGRAM_BOT_TOKEN')))")

mkdir -p logs
PIDS=()
start() {  # start <name> <args...>
  local name="$1"; shift
  "$PY" "$@" > "logs/$name.log" 2>&1 &
  PIDS+=($!)
  echo "  started: $name (pid $!, log: logs/$name.log)"
}
stop_all() {
  echo; echo "Stopping everything..."
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap stop_all INT TERM EXIT

wait_for() {  # wait_for <url>: up to 90 s
  for _ in $(seq 1 90); do
    "$PY" -c "import sys, requests; sys.exit(0 if requests.get('$1', timeout=2).ok else 1)" 2>/dev/null && return 0
    sleep 1
  done
  echo "  $1 did not answer after 90 s - check the log."
}

echo; echo "Starting services..."
start offers_api -m uvicorn offers.api:app --port 8001
start main_api -m uvicorn api.main:app --port 8010
wait_for http://127.0.0.1:8001/health
wait_for http://127.0.0.1:8010/health
start storefront -m streamlit run ui/app.py --server.port 8501 --server.headless true
start control_room -m streamlit run ui/control_room.py --server.port 8502 --server.headless true

BOT_STATE="skipped (no TELEGRAM_BOT_TOKEN in .env)"
if [ "$NO_BOT" = 1 ]; then BOT_STATE="skipped (--no-bot)"
elif [ "$HAS_BOT" = 1 ]; then start bot -m bot.main; BOT_STATE="running (long polling)"; fi

SCHED_STATE="skipped (--no-scheduler)"
if [ "$NO_SCHEDULER" = 0 ]; then
  if [ "$DRY_RUN" = 1 ]; then start scheduler -m pipeline.scheduler --dry-run; SCHED_STATE="DRY RUN - prints, never sends"
  else start scheduler -m pipeline.scheduler; SCHED_STATE="LIVE - every 15 min, reco retrain 03:00"; fi
fi

cat <<EOF

================ What's running where ================
  Offers API    http://127.0.0.1:8001/docs   (needs X-API-Key)
  Main API      http://127.0.0.1:8010/docs   (/health shows real vs stub modules)
  Storefront    http://localhost:8501
  Admin         http://localhost:8502   (create offers, assistant, rules, results)
  Telegram bot  $BOT_STATE
  Scheduler     $SCHED_STATE
  Logs          logs/*.log
======================================================
Demo script: ui/DEMO.md. Press Ctrl+C to stop everything.
EOF
wait
