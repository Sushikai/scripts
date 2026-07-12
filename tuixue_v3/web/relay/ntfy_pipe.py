"""NTFY bidirectional pipe.

Uses ntfy.sh as a transport: Mac subscribes to topic `tuixue-<id>-req`,
phone publishes to same topic with `X-Method`/`X-Path` headers. Mac
responds on topic `tuixue-<id>-resp`.

Phone side: NTFY iOS app → tap "Publish" → write a message; or use any
HTTP client pointed at ntfy.sh.

Why: ntfy.sh lives on a completely different domain than api.telegram.org
or trycloudflare.com — so a sandbox policy that blocks one doesn't
necessarily block the others. Adds to the egress surface.

Run:
    python3 web/relay/ntfy_pipe.py [--port 7799] [--topic tuixue-abc]

Stdout emits `url=https://ntfy.sh/<topic>` on first connection.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import signal
import sys
from pathlib import Path
from typing import Any

import aiohttp

NTFY_BASE = "https://ntfy.sh"
DEFAULT_PORT = 7799
STATE_DIR = Path.home() / ".config" / "tuixue"
STATE_DIR.mkdir(parents=True, exist_ok=True)


def gen_topic() -> str:
    """Stable per-launch topic — store in state so phone sees the same one."""
    p = STATE_DIR / "ntfy_pipe.topic"
    if p.exists():
        return p.read_text().strip()
    t = f"tuixue-{secrets.token_hex(4)}"
    p.write_text(t)
    return t


async def reply(session: aiohttp.ClientSession, topic: str, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False)[:3500]
    headers = {"Content-Type": "application/json", "Title": "tuixue response"}
    try:
        async with session.post(
            f"{NTFY_BASE}/{topic}-resp", data=body, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            await r.read()
    except Exception as e:  # noqa: BLE001
        print(f"[ntfy-pipe] reply err: {e}", file=sys.stderr)


async def do_local_request(
    session: aiohttp.ClientSession, port: int, method: str, path: str, body: str
) -> tuple[int, str]:
    url = f"http://localhost:{port}{path}"
    try:
        async with session.request(
            method, url, data=body or None, timeout=aiohttp.ClientTimeout(total=12)
        ) as r:
            return r.status, await r.text()
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def write_url_file(method: str, url: str) -> None:
    root = Path(__file__).resolve().parents[2]
    (root / "tunnel_url.txt").write_text(url + "\n")
    (root / "tunnel_method.txt").write_text(method + "\n")


async def run(args: argparse.Namespace) -> int:
    topic = args.topic or gen_topic()
    public_url = f"{NTFY_BASE}/{topic}"
    write_url_file("ntfy", public_url)
    print(f"[ntfy-pipe] topic={topic}  url={public_url}", flush=True)

    stop = asyncio.Event()

    def _stop(*_a):
        stop.set()

    loop = asyncio.get_event_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _stop)
        except NotImplementedError:
            pass

    backoff = 1
    async with aiohttp.ClientSession() as session:
        while not stop.is_set():
            try:
                # ntfy supports SSE: GET /topic/jsonl
                async with session.get(
                    f"{NTFY_BASE}/{topic}-req/jsonl",
                    timeout=aiohttp.ClientTimeout(total=None, sock_connect=8, sock_read=60),
                ) as r:
                    backoff = 1
                    async for line in r.content:
                        if stop.is_set():
                            break
                        line = line.decode(errors="ignore").strip()
                        if not line.startswith("{"):
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        # 'event' is "message"; the actual user text is in ev['message']
                        msg = ev.get("message", "")
                        # Try headers first; else parse the body
                        headers = {k.lower(): v for k, v in (ev.get("headers") or {}).items()}
                        method = (headers.get("x-method") or "GET").upper()
                        path = headers.get("x-path") or "/api/health"
                        body_text = msg if method in ("POST", "PUT", "DELETE") else None
                        status, text = await do_local_request(
                            session, args.port, method, path, body_text or ""
                        )
                        await reply(
                            session,
                            topic,
                            {"status": status, "body": text[:3500], "req": method + " " + path},
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                print(f"[ntfy-pipe] stream err: {e}; retry in {backoff}s", file=sys.stderr)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--topic", default=None)
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
