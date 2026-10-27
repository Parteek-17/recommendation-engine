// Cloudflare Worker: a permanent public address for the Admin dashboard.
//
//   https://quiet-sun-08f8.parteeksharma2006.workers.dev  ->  current trycloudflare.com tunnel  ->  laptop :8502
//
// The quick tunnel's address changes on every restart; only TUNNEL_URL (a Worker variable)
// needs updating, so the workers.dev link people have stays the same.
// It redirects instead of proxying: Streamlit's live WebSocket does not survive being proxied
// through a Worker to a quick tunnel. The dashboard's own password (ADMIN_PASSWORD) protects it.

export default {
  async fetch(request, env) {
    if (!env.TUNNEL_URL) {
      return new Response("The dashboard is offline (no tunnel configured).", { status: 503 });
    }
    const incoming = new URL(request.url);
    const target = new URL(incoming.pathname + incoming.search, env.TUNNEL_URL);
    return Response.redirect(target.toString(), 302);
  },
};
