"""Cloudflare Worker WebSocket client — runs on Mac.

Opens an outbound WSS to the deployed Cloudflare Worker (which forwards bytes
between Mac and iPhone in the same Durable Object room). All HTTP traffic to
localhost:7799 gets piped through the WSS to the phone, and vice-versa.

For HTTP this is just a TCP-level proxy inside the WSS frame. Each request is
serialized as a tiny envelope:
  -> {"id": "<uuid>", "method": "GET", "path": "/api/health", "body": ""}
  <- {"id": "<uuid>", "status": 200, "body": "..."}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

import aiohttp
import websockets  # type: ignore[import-not-found]


async def run(args: argparse.Namespace) -> int:
    wss = args.wss.rstrip("/") + f"/relay/{args.session}"
    ws_url = wss.replace("https://", "wss://").replace("http://", "ws://")
    pending: dict[str, asyncio.Future] = {}

    print(f"[cf-client] connecting {ws_url}", flush=True)

    async def pump_http_to_ws() -> None:
        async with aiohttp.ClientSession() as session:
            while True:
                rid, method, path, body, fut = await args.queue.get()
                try:
                    async with session.request(
                        method,
                        f"http://localhost:{args.port}{path}",
                        data=body or None,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as r:
                        text = await r.text()
                        fut.set_result((r.status, text))
                except Exception as e:  # noqa: BLE001
                    fut.set_result((0, f"{type(e).__name__}: {e}"))

    async def ws_loop() -> None:
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({"role": "mac"}))
            print("[cf-client] hello sent", flush=True)
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("sys"):  # control message
                    print(f"[cf-client] sys: {msg}", flush=True)
                    continue
                rid = msg.get("id")
                fut = pending.pop(rid, None)
                if fut and not fut.done():
                    fut.set_result((msg.get("status", 0), msg.get("body", "")))

    async def pipe_request(method: str, path: str, body: str = "") -> tuple[int, str]:
        """Called by the consumer (a thin HTTP reverse proxy on port 8081)."""
        rid = uuid.uuid4().hex[:12]
        fut = asyncio.get_event_loop().create_future()
        pending[rid] = fut
        await ws.send(json.dumps({"id": rid, "method": method, "path": path, "body": body}))
        return await asyncio.wait_for(fut, timeout=30)

    queue: asyncio.Queue = asyncio.Queue()

    class _Args:
        port = args.port
        queue = queue

    args = _Args()

    # Spawn minimal HTTP server on 8081 that proxies each request to pipe_request
    from aiohttp import web

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
    print(f"[cf-client] HTTP proxy listening on 127.0.0.1:{args.http_port}", flush=True)
    print(f"[cf-client] phone should hit https://<workers.dev>/ via the worker DO", flush=True)

    try:
        await ws_loop()
    except KeyboardInterrupt:
        pass
    finally:
        await runner.cleanup()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wss", required=True, help="workers.dev URL of the relay")
    ap.add_argument("--session", required=True, help="session id (4-32 chars)")
    ap.add_argument("--port", type=int, default=7799, help="local FastAPI port")
    ap.add_argument(
        "--http-port", type=int, default=8081, help="local HTTP proxy port (rare)"
    )
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
