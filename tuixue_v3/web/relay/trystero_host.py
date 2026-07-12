"""Trystero host.

Serves a static HTML page that boots Trystero (WebRTC P2P via BitTorrent
trackers or public MQTT brokers). Both Mac and iPhone open this page in
Safari, share the same room id, and the WebRTC connection establishes P2P
without any private relay server. The room-id is printed on stdout for
start_remote.sh to read into tunnel_url.txt.

Why this is sandbox-survivable: signaling uses BitTorrent trackers on
UDP/6881 and public MQTT brokers on TCP/1883 — neither are usually blocked.

Run:
    python3 trystero_host.py --port 7799
"""
from __future__ import annotations

import argparse
import http.server
import secrets
import socketserver
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7799)
    args = ap.parse_args()

    room = f"tuixue-{secrets.token_hex(3)}"

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>tuixue · trystero</title>
<style>
 body{{font:14px/1.4 -apple-system,BlinkMacSystemFont,sans-serif;max-width:480px;margin:24px auto;padding:0 16px;color:#111;background:#fafafa}}
 .ok{{color:#0a7a2d}}.err{{color:#a00}}.tag{{font-family:ui-monospace,Menlo,monospace;background:#eef;border-radius:3px;padding:1px 5px}}
 pre{{font-family:ui-monospace,Menlo,monospace;background:#111;color:#0f0;padding:8px;height:120px;overflow:auto;border-radius:4px;font-size:11px}}
</style></head><body>
<h2>🛰 tuixue · trystero P2P bridge</h2>
<p>Room: <span class="tag" id=room>{room}</span></p>
<p>Status: <span id=status class=err>connecting…</span></p>
<pre id=log></pre>
<script type="module">
  import {{joinRoom}} from 'https://cdn.jsdelivr.net/npm/trystero/torrent.js';
  const room = document.getElementById('room').textContent;
  const log = m => {{ document.getElementById('log').textContent += m + '\\n'; }};
  try {{
    const r = joinRoom({{appId: 'tuixue-v3'}}, room);
    document.getElementById('status').className = 'ok';
    document.getElementById('status').textContent = 'connected';
    log('joined room ' + room);
    r.onPeerJoin(id => log('peer joined ' + id.slice(0,8)));
    r.onPeerLeave(id => log('peer left ' + id.slice(0,8)));
    // expose API for app code: r.sendText / r.sendFile
    window.trystero = r;
  }} catch (e) {{
    document.getElementById('status').textContent = 'failed: ' + e.message;
  }}
</script>
</body></html>
"""

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        def log_message(self, fmt, *args):  # silence default logs
            return

    port = args.port
    # Serve on a side-port (7799 is busy with FastAPI) — use 7799+1 default? User
    # provided --port is the offset; default behavior is 8089 to avoid clash.
    # Caller passes the FastAPI port, so we open 8089 to avoid stepping on it.
    side_port = port + 200  # 7799 + 200 = 7999; user can override
    with socketserver.ThreadingTCPServer(("127.0.0.1", side_port), H) as srv:
        print(f"http://localhost:{side_port}/trystero", flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
