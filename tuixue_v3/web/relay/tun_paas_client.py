"""PaaS relay client.

Mirror of `tun_cf_client.py` for non-Cloudflare PaaS (Koyeb/Render/Fly/HF).
Same protocol, same role handshake (`{"role":"mac"}` first), same JSON
envelopes. The relay server code is `relay_node.js`.
"""
from __future__ import annotations
import asyncio
import json
import sys
import uuid
import argparse

import aiohttp
import websockets  # type: ignore[import-not-found]
from aiohttp import web


async def run(args: argparse.Namespace) -> int:
    wss = args.wss.rstrip("/") + f"/relay/{args.session}"
    ws_url = wss if wss.startswith(("ws://", "wss://")) else "wss://" + wss.lstrip("https://").lstrip("http://")
    pending: dict[str, asyncio.Future] = {}

    print(f"[paas-client] connecting {ws_url}", flush=True)

    async def ws_loop() -> None:
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({"role": "mac"}))
            print("[paas-client] hello sent", flush=True)
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                rid = msg.get("id")
                if rid is None:
                    continue
                fut = pending.pop(rid, None)
                if fut and not fut.done():
                    fut.set_result((msg.get("status", 0), msg.get("body", "")))

    async def pipe_request(method: str, path: str, body: str = "") -> tuple[int, str]:
        rid = uuid.uuid4().hex[:12]
        fut = asyncio.get_event_loop().create_future()
        pending[rid] = fut
        # Need to send via the same ws — store reference
        pipe_request._send({"id": rid, "method": method, "path": path, "body": body})
        return await asyncio.wait_for(fut, timeout=30)

    send_q: asyncio.Queue = asyncio.Queue()

    async def ws_send_loop() -> None:
        async with websockets.connect(ws_url) as ws:
            pipe_request._send = lambda msg: asyncio.create_task(_send(ws, msg))

    async def _send(ws, msg):
        await ws.send(json.dumps(msg))

    async def handle(req: web.Request) -> web.Response:
        body = await req.read()
        status, text = await pipe_request(req.method, req.path, body.decode("utf-8", "ignore"))
        return web.Response(text=text, status=status)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", args.http_port)
    await site.start()
    print(f"[paas-client] proxy listening on 127.0.0.1:{args.http_port}", flush=True)

    try:
        # Spawn both: ws_loop for read, ws_send_loop for write
        await asyncio.gather(ws_send_loop(), ws_loop())
    except KeyboardInterrupt:
        pass
    finally:
        await runner.cleanup()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wss", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--port", type=int, default=7799)
    ap.add_argument("--http-port", type=int, default=8081)
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
