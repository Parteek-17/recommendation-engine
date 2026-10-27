#!/usr/bin/env bash
# Render start command: restore the ready-made store, start the backend, serve the Admin dashboard.
#
# Render's free disk is wiped on every restart, so each start unpacks the same small synthetic
# store (1,000 customers, seed 42) and its trained models from deploy/render/seed/. Live activity
# since the last restart (offers sent, demo testers) is lost on restart; the store itself is identical.
set -euo pipefail
cd "$(dirname "$0")/../.."

if [ ! -f data/app.db ]; then
  mkdir -p data models
  gunzip -c deploy/render/seed/app.db.gz > data/app.db
  cp deploy/render/seed/reco_bundle.pkl models/reco_bundle.pkl
  echo "Restored the ready-made store into data/app.db"
fi

# Offers API + scheduler + Telegram bot, in one process to stay under 512 MB.
python deploy/render/backend.py &

# The only public part: the Admin dashboard on Render's port (password: ADMIN_PASSWORD).
exec python -m streamlit run ui/control_room.py \
  --server.port "${PORT:-8502}" --server.address 0.0.0.0 --server.headless true \
  --browser.gatherUsageStats false
