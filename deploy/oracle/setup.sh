#!/usr/bin/env bash
# One-time setup of the whole project on an Oracle Cloud "Always Free" Ubuntu VM.
#
#   1. Copy your .env to the server first (from your laptop):
#        scp -i <key> .env ubuntu@<server-ip>:~/store.env
#   2. On the server:
#        curl -fsSL https://raw.githubusercontent.com/Parteek-17/recommendation-engine/main/deploy/oracle/setup.sh | bash
#      (or: git clone ... && bash recommendation-engine/deploy/oracle/setup.sh)
#
# What it does: installs Python + build tools + cloudflared, clones the repo to ~/store,
# makes a virtualenv, installs packages, generates the synthetic data and trains the models
# (scripts/preflight.py, ~10-20 min the first time), then installs systemd services so
# everything starts on boot and restarts if it crashes. Safe to run again (it updates).
set -euo pipefail

REPO="${REPO:-https://github.com/Parteek-17/recommendation-engine.git}"
APP="$HOME/store"
USER_NAME="$(whoami)"

echo "== 1/6 system packages"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-dev build-essential git curl
if ! command -v cloudflared >/dev/null; then
  arch="$(dpkg --print-architecture)"   # arm64 on the free Ampere VM, amd64 on the free micro VM
  curl -fsSL -o /tmp/cloudflared.deb \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}.deb"
  sudo dpkg -i /tmp/cloudflared.deb
fi

echo "== 2/6 code"
if [ -d "$APP/.git" ]; then git -C "$APP" pull --ff-only; else git clone "$REPO" "$APP"; fi
cd "$APP"

echo "== 3/6 secrets"
if [ ! -f .env ]; then
  if [ -f "$HOME/store.env" ]; then mv "$HOME/store.env" .env
  else echo "No .env: copy yours first (scp ... .env ubuntu@<ip>:~/store.env), then run this again."; exit 1; fi
fi
chmod 600 .env

echo "== 4/6 python packages"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip wheel
.venv/bin/pip install -r requirements.txt

echo "== 5/6 data + models (first run takes a while)"
.venv/bin/python scripts/preflight.py

echo "== 6/6 always-on services"
for unit in deploy/oracle/systemd/*.service; do
  sed -e "s|__APP__|$APP|g" -e "s|__USER__|$USER_NAME|g" "$unit" | sudo tee "/etc/systemd/system/$(basename "$unit")" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now store-offers-api store-main-api store-admin store-bot store-scheduler store-tunnel

sleep 20
echo ""
echo "Done. Services:"
systemctl --no-pager --plain list-units 'store-*' | head -n 10
echo ""
echo "Public dashboard address (put it in the Worker's TUNNEL_URL):"
bash "$APP/deploy/oracle/tunnel_url.sh"
