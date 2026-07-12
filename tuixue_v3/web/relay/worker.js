/**
 * Cloudflare Worker + Durable Object acting as bidirectional WebSocket relay.
 *
 * Design
 * ------
 * Both Mac-side (`tailscale`-like outbound client) and iPhone Safari connect
 * WSS to `wss://<your-worker>.workers.dev/relay/<sessionId>`. The Worker
 * hands the connection to a Durable Object keyed by sessionId. The DO holds
 * a Set of WebSockets and forwards every message except the first
 * (which is `"mac"` or `"phone"` to declare identity).
 *
 * Limits (free tier)
 *   100k req/day, 32 concurrent DO WS, 15-min idle disconnect.
 *   That's fine for interactive dashboard use; rotate session on reconnect.
 *
 * Deploy
 * ------
 *   npm i -g wrangler
 *   cd web/relay && wrangler deploy worker.js --name tuixue-relay
 * The first deploy prints `https://tuixue-relay.<acct>.workers.dev`; write
 * to ~/.config/tuixue/relays.json so start_remote.sh can pick it up.
 */
export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === '/' || path === '/index.html') {
      return new Response(LANDING_HTML, {
        headers: { 'content-type': 'text/html; charset=utf-8' },
      });
    }

    const m = path.match(/^\/relay\/([a-zA-Z0-9_-]{4,32})$/);
    if (!m) {
      return new Response('not found', { status: 404 });
    }
    const sessionId = m[1];

    if (request.headers.get('upgrade') !== 'websocket') {
      return new Response('expect WebSocket', { status: 426 });
    }

    const id = env.RELAY_DO.idFromName(sessionId);
    const stub = env.RELAY_DO.get(id);
    return stub.fetch(request);
  },
};

// Durable Object
export class RelayRoom {
  constructor(state) {
    this.state = state;
    this.sockets = new Set(); // {ws, role}
    this.meta = new Map();    // ws -> { role, label }
    state.setWebSocketEvent?.();
  }

  async fetch(request) {
    if (request.headers.get('upgrade') !== 'websocket') {
      return new Response('expect WebSocket', { status: 426 });
    }
    const pair = new WebSocketPair();
    const ws = pair[0];
    const other = pair[1];
    this.state.acceptWebSocket?.(other);
    return new Response(null, { status: 101, webSocket: ws });
  }

  async webSocketMessage(ws, raw) {
    const meta = this.meta.get(ws);
    let role = meta?.role;
    let body = raw;
    if (!role) {
      // First message declares role: 'mac' or 'phone'
      try {
        const m = JSON.parse(raw);
        role = (m.role || m.who || '').toLowerCase();
        body = m.data !== undefined ? (typeof m.data === 'string' ? m.data : JSON.stringify(m.data)) : '';
      } catch (_) {
        role = String(raw).slice(0, 8).toLowerCase();
        body = '';
      }
      if (role !== 'mac' && role !== 'phone') {
        role = 'peer';
      }
      this.meta.set(ws, { role });
      this._broadcast(ws, JSON.stringify({ sys: 'peer-joined', role }));
    }
    body = typeof body === 'string' ? body : new TextDecoder().decode(body);
    this._broadcast(ws, body);
  }

  async webSocketClose(ws, code) {
    const meta = this.meta.get(ws);
    this.meta.delete(ws);
    this._broadcast(ws, JSON.stringify({ sys: 'peer-left', role: meta?.role, code }));
  }

  _broadcast(src, payload) {
    for (const peer of this.state.getWebSockets ? this.state.getWebSockets() : []) {
      if (peer === src) continue;
      try {
        peer.send(payload);
      } catch (_) {
        // ignore
      }
    }
  }
}

const LANDING_HTML = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>tuixue relay</title>
<style>
  body{font:14px/1.4 -apple-system,BlinkMacSystemFont,sans-serif;max-width:560px;margin:32px auto;padding:0 16px;color:#222}
  code,pre{font-family:ui-monospace,Menlo,monospace;background:#f3f3f3;padding:2px 5px;border-radius:4px}
  pre{padding:10px;overflow:auto}
  button{padding:6px 10px;border:1px solid #ccc;border-radius:5px;background:#fafafa;cursor:pointer}
</style></head><body>
<h1>tuixue WSS relay</h1>
<p>This is a Cloudflare Worker Durable Object acting as a bidirectional WebSocket
relay between the Mac client and the iPhone Safari page.</p>
<ol>
  <li>Mac opens <code>wss://&lt;this-host&gt;/relay/&lt;sessionId&gt;</code> and sends <code>{"role":"mac"}</code> first.</li>
  <li>Phone opens the same URL and sends <code>{"role":"phone"}</code> first.</li>
  <li>Subsequent messages are forwarded byte-for-byte between roles.</li>
</ol>
<pre id=log style="background:#111;color:#0f0;height:160px"></pre>
<input id=sid placeholder="session id (4-32 chars)">
<button id=join>Connect</button>
<script>
  const log = m => { const e = document.getElementById('log'); e.textContent += m + "\\n"; };
  document.getElementById('join').onclick = () => {
    const sid = document.getElementById('sid').value.trim();
    if (!sid) return;
    const ws = new WebSocket((location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + location.host + '/relay/' + sid);
    ws.onopen = () => { log('open'); ws.send(JSON.stringify({role:'phone'})); };
    ws.onmessage = ev => log('← ' + (ev.data.length > 200 ? ev.data.slice(0,200)+'…' : ev.data));
    ws.onclose = () => log('closed');
  };
</script>
</body></html>
`;
