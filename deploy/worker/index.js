// Cloudflare Worker: a permanent public address for the Admin dashboard.
//
//   https://quiet-sun-08f8.parteeksharma2006.workers.dev  ->  TUNNEL_URL
//
// TUNNEL_URL is where the dashboard runs: the Render service (https://<name>.onrender.com) or a
// trycloudflare.com tunnel to a laptop/VM. Change only that variable; the workers.dev link stays.
// It redirects instead of proxying: Streamlit's live WebSocket does not survive being proxied.
//
// Keep-awake: Render's free plan sleeps after ~15 min without visits, which would also stop the
// Telegram bot. A Cron Trigger (Worker → Settings → Triggers → "*/10 * * * *") calls scheduled()
// every 10 minutes and pings the health check, so it never sleeps.

export default {
  async fetch(request, env) {
    if (!env.TUNNEL_URL) {
      return new Response("The dashboard is offline (no address configured).", { status: 503 });
    }
    const incoming = new URL(request.url);
    const target = new URL(incoming.pathname + incoming.search, env.TUNNEL_URL);
    return Response.redirect(target.toString(), 302);
  },

  async scheduled(event, env, ctx) {
    if (env.TUNNEL_URL) {
      ctx.waitUntil(fetch(new URL("/_stcore/health", env.TUNNEL_URL)).catch(() => {}));
    }
  },
};
