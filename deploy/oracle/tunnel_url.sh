#!/usr/bin/env bash
# Print the current quick-tunnel address of the Admin dashboard (it changes when the tunnel restarts).
# Put it in the Cloudflare Worker's TUNNEL_URL variable; the workers.dev link stays the same.
for _ in $(seq 1 30); do
  url="$(journalctl -u store-tunnel --no-pager -o cat 2>/dev/null | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -n 1)"
  [ -n "$url" ] && { echo "$url"; exit 0; }
  sleep 2
done
echo "No tunnel address yet: check  sudo journalctl -u store-tunnel -n 30"; exit 1
