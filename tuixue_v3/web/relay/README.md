# tuixue_v3 remote-access bridge modules

These implement the 2026-07-12 access-stabilization ladder. `start_remote.sh`
will try each module in priority order, fast-failing on install miss or unreachability.

## Files

| File | Purpose | One-time setup |
|------|---------|----------------|
| `telegram_bridge.py` | Bidirectional HTTP-over-Telegram. Phone sends `GET /api/health`, bot replies with the response body. | Create a TG bot via @BotFather, put `TELEGRAM_BOT_TOKEN` in `~/.hermes/env.sh`. The phone opens the @<bot> in TG app. **No URL** — interaction is via chat messages. |
| `ntfy_pipe.py` | Two-way pipe over `ntfy.sh/<topic>`. | Phone installs NTFY iOS app (free). Phone subscribes to the topic; or opens `https://ntfy.sh/<topic>` in Safari. |
| `mqtt_bridge.py` | MQTT-over-TLS via `broker.hivemq.com:8883`. Different protocol than HTTPS — survives hostile networks. | Phone installs any free MQTT iOS app (e.g. MQTT Dash). Subscribes to `tuixue/<session>/resp`, publishes to `tuixue/<session>/req` with JSON `{method,path,body}`. Session id is printed in the start log. |
| `trystero_host.py` | HTML page that runs Trystero (WebRTC P2P over BitTorrent trackers / MQTT brokers). | Phone opens same URL on Safari. No install. |
| `tun_cf_client.py` | Mac → Cloudflare Worker Durable Object WebSocket client. | `wrangler deploy worker.js` once; write URL to `~/.config/tuixue/relays.json` as `cf_worker`. |
| `relay_node.js` | Same role, deployable to Koyeb/Render/Fly/HF as a Node WS server. Dockerfile included. | One-time docker deploy. |
| `tun_paas_client.py` | Mac client for the PaaS relay. | Add the deployed URL to `~/.config/tuixue/relays.json` as `paas_relay` and `paas_relay_wss`. |
| `worker.js` + `wrangler.toml` | Cloudflare Worker + Durable Object source. | `npm i -g wrangler && wrangler deploy worker.js` |

## relays.json layout

```json
{
  "cf_worker":      "https://tuixue-relay.<acct>.workers.dev",
  "paas_relay":     "https://tuixue-relay.onrender.com",
  "paas_relay_wss": "wss://tuixue-relay.onrender.com"
}
```

Written by `install_relay.sh` after one-time deploys.

## Sandbox-survival ranking

The 9 mechanisms in priority order, ranked by which egress surface is most likely to be allowed in a restrictive network:

1. Tailscale (controlplane + DERP on TLS/443, near-universally allowed)
2. ZeroTier (planet root 443, alternate control plane)
3. Telegram bot (api.telegram.org — the canonical whitelist)
4. NTFY (ntfy.sh — different domain surface)
5. MQTT (broker.hivemq.com:8883 — different *protocol* surface)
6. Cloudflare Worker (workers.dev — universal whitelist)
7. Generic PaaS relay
8. Trystero (BitTorrent trackers UDP/6881 — survives even when HTTP is filtered)
9. Existing tunnels (kept as backstop in case network is loosened)
