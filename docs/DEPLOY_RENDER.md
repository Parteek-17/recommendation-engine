# Deploy on Render (free plan)

One free Render web service runs the whole store: the Admin dashboard (public, password-protected)
plus one background process with the Offers API, the 15-minute offer scheduler and the Telegram bot.
Files: `render.yaml`, `deploy/render/` (start script, combined backend, ready-made store).

**Free plan trade-offs (accepted on purpose):**
- **Small store**: 1,000 synthetic customers (not 10,000), so everything fits in 512 MB
  (measured peak ≈ 460 MB). Data and models ship ready-made in `deploy/render/seed/`.
- **Resets on restart**: Render's free disk is wiped on every restart/deploy. The store comes back
  identical; live activity since the last restart (offers sent, demo testers) is lost.
- **Sleep**: free services sleep after ~15 min without visits, which would stop the bot. The
  Cloudflare Worker's Cron Trigger pings it every 10 min so it stays awake.
- **Only one bot**: stop the "Telegram bot" window on your laptop first (Telegram allows one connection).

## Steps

1. **Render**: https://dashboard.render.com → **New → Blueprint** → connect GitHub → pick
   `Parteek-17/recommendation-engine` → **Apply**.
2. It asks for three secrets. Copy them from your laptop's `.env`:
   `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `ADMIN_PASSWORD`.
3. Wait for the build and first start (~5–10 min). The service URL looks like
   `https://ecommerce-store-admin.onrender.com`. Open it → sign in with `ADMIN_PASSWORD`.
4. **Cloudflare Worker** (`quiet-sun-08f8`):
   - paste the latest code from `deploy/worker/index.js` → **Deploy**
   - **Settings → Variables**: set `TUNNEL_URL` to the Render URL from step 3
   - **Settings → Triggers → Cron Triggers → Add**: `*/10 * * * *`
5. Test: https://quiet-sun-08f8.parteeksharma2006.workers.dev opens the dashboard; `/start` on
   @Sutlejbot answers.

Logs: Render dashboard → the service → **Logs**. Redeploy after a code change: **Manual Deploy**.
