"""MQTT-over-TLS bidirectional bridge via public HiveMQ broker.

Different protocol stack from HTTPS — survives sandboxes that aggressively
filter HTTP/S but leave MQTT-over-TLS alone. broker.hivemq.com:8883 is a
well-known free public broker.

Topic layout:
    tuixue/<session_id>/req    phone -> mac (JSON {method, path, body})
    tuixue/<session_id>/resp   mac  -> phone (JSON {status, body})

Phone client: any free MQTT iOS app (MQTT Dash, MQTT Toolbox). Configure:
    host: broker.hivemq.com, port: 8883, TLS on
    sub:  tuixue/<session_id>/resp
    pub:  tuixue/<session_id>/req with JSON payloads

Run:
    python3 web/relay/mqtt_bridge.py [--port 7799]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import signal
import sys
import time
from pathlib import Path

import aiohttp
import aiomqtt as asyncio_mqtt  # type: ignore[import-not-found]  # was asyncio-mqtt, renamed

BROKER_HOST = "broker.hivemq.com"
BROKER_PORT = 8883
DEFAULT_PORT = 7799
STATE_DIR = Path.home() / ".config" / "tuixue"
STATE_DIR.mkdir(parents=True, exist_ok=True)


def get_session() -> str:
    p = STATE_DIR / "mqtt_bridge.session"
    if p.exists():
        return p.read_text().strip()
    s = secrets.token_hex(6)
    p.write_text(s)
    return s


def write_url_file(method: str, url: str) -> None:  # pragma: no cover - 兼容老调用
    # 2026-07-12: 禁用 — mqtt://broker... 不是 HTTP URL,会污染前端 QR
    # 现在 mqtt_bridge 走 /tmp/tuixue_tunnels/mqtt_bridge.ready sentinel,
    # 由 server.py /api/tunnel/status 自动检测并展示。
    return


async def do_local(
    session: aiohttp.ClientSession, port: int, method: str, path: str, body: str
) -> tuple[int, str]:
    url = f"http://localhost:{port}{path}"
    try:
        async with session.request(
            method,
            url,
            data=body or None,
            timeout=aiohttp.ClientTimeout(total=12),
        ) as r:
            return r.status, await r.text()
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


async def run(args: argparse.Namespace) -> int:
    sid = get_session()
    req_topic = f"tuixue/{sid}/req"
    resp_topic = f"tuixue/{sid}/resp"
    info = (
        f"mqtt://{BROKER_HOST}:{BROKER_PORT} | "
        f"sub={resp_topic} | pub={req_topic}"
    )
    write_url_file("mqtt", info)
    print(f"[mqtt-bridge] session={sid} {info}", flush=True)

    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, stop.set)
        except NotImplementedError:
            pass

    backoff = 1
    while not stop.is_set():
        try:
            async with asyncio_mqtt.Client(
                hostname=BROKER_HOST, port=BROKER_PORT, tls=True, keepalive=30
            ) as client:
                async with client.messages() as stream:
                    await client.subscribe(resp_topic.replace("resp", "req"))
                    backoff = 1
                    async for message in stream:
                        if stop.is_set():
                            break
                        payload = message.payload.decode(errors="ignore")
                        try:
                            req = json.loads(payload)
                        except json.JSONDecodeError:
                            req = {"method": "GET", "path": "/api/health", "body": ""}
                        method = (req.get("method") or "GET").upper()
                        path = req.get("path") or "/api/health"
                        body = req.get("body") or ""
                        async with aiohttp.ClientSession() as s:
                            status, text = await do_local(s, args.port, method, path, body)
                        resp = json.dumps(
                            {"status": status, "body": text[:3500], "ts": time.time()}
                        )
                        await client.publish(resp_topic, resp, qos=0)
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            print(f"[mqtt-bridge] err: {e}; retry in {backoff}s", file=sys.stderr)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
