/**
 * Tiny Node WS relay — runs on Koyeb / Render / Fly.io / HF Spaces / anywhere.
 * Pairs Mac and phone in same room by session id; forwards bytes either way.
 *
 * Run: node relay_node.js  (PORT from env, default 8080)
 */
'use strict';

const http = require('http');
const express = require('express');
const { WebSocketServer } = require('ws');

const app = express();
app.get('/', (_req, res) =>
  res.type('text/html').send(landingHtml())
);
app.get('/healthz', (_req, res) =>
  res.json({ ok: true, ts: Date.now() })
);

const server = http.createServer(app);
const wss = new WebSocketServer({ server });

/** room: Map<sessionId, {mac?: WebSocket, phone?: WebSocket}> */
const rooms = new Map();

function getRoom(sid) {
  let r = rooms.get(sid);
  if (!r) {
    r = { mac: null, phone: null };
    rooms.set(sid, r);
  }
  return r;
}

function broadcast(room, fromWs, payload) {
  for (const ws of [room.mac, room.phone]) {
    if (!ws || ws === fromWs) continue;
    if (ws.readyState === 1) ws.send(payload);
  }
}

function logLine(...x) {
  console.log(`[relay ${new Date().toISOString()}]`, ...x);
}

wss.on('connection', (ws, req) => {
  const url = new URL(req.url, 'http://x');
  const m = url.pathname.match(/^\/relay\/([a-zA-Z0-9_-]{4,32})$/);
  if (!m) {
    ws.close(1008, 'bad path');
    return;
  }
  const sid = m[1];
  const room = getRoom(sid);
  ws._sid = sid;
  ws._role = null;

  ws.on('message', (data, isBinary) => {
    let payload = isBinary ? data : data.toString();
    if (!ws._role) {
      try {
        const j = JSON.parse(payload);
        const r = (j.role || '').toLowerCase();
        if (r === 'mac' || r === 'phone') {
          ws._role = r;
          room[r] = ws;
          logLine(sid, 'role=', r);
          return;
        }
      } catch (_) { /* not JSON, assume mid-stream */ }
      // Default: first to join is mac, second is phone
      ws._role = room.mac ? 'phone' : 'mac';
      room[ws._role] = ws;
      logLine(sid, 'role=', ws._role, '(default)');
    }
    if (!isBinary) payload = payload.toString();
    broadcast(room, ws, payload);
  });

  ws.on('close', () => {
    if (ws._role === 'mac') room.mac = null;
    if (ws._role === 'phone') room.phone = null;
    logLine(sid, 'close role=', ws._role);
  });
});

const port = Number(process.env.PORT) || 8080;
server.listen(port, () => logLine('listen', port));

function landingHtml() {
  return `<!doctype html><meta charset=utf-8>
<title>tuixue relay</title>
<style>
  body{font:14px/1.5 -apple-system,BlinkMacSystemFont,sans-serif;max-width:560px;margin:32px auto;padding:0 16px;color:#222}
  code{font-family:ui-monospace,Menlo,monospace;background:#f3f3f3;padding:2px 5px;border-radius:4px}
  pre{font-family:ui-monospace,Menlo,monospace;background:#111;color:#0f0;padding:10px;height:160px;overflow:auto;border-radius:4px}
  button{padding:6px 10px;border:1px solid #ccc;border-radius:5px;background:#fafafa;cursor:pointer}
</style>
<h1>tuixue WSS relay (node)</h1>
<p>Mac sends <code>{"role":"mac"}</code>, phone sends <code>{"role":"phone"}</code>, then both directions forward.</p>
<input id=sid placeholder="session id">
<button id=join>Connect</button>
<pre id=log></pre>
<script>
  const log=m=>document.getElementById('log').textContent+=m+"\\n";
  document.getElementById('join').onclick=()=>{
    const sid=document.getElementById('sid').value.trim(); if(!sid)return;
    const ws=new WebSocket((location.protocol==='https:'?'wss:':'ws:')+'//'+location.host+'/relay/'+sid);
    ws.onopen=()=>{log('open');ws.send(JSON.stringify({role:'phone'}));};
    ws.onmessage=ev=>log('← '+(ev.data.length>200?ev.data.slice(0,200)+'…':ev.data));
    ws.onclose=()=>log('closed');
  };
</script>`;
}
