"""/api/tunnel-status:读 tunnel_url.txt + 探测后台 tunnel 进程 + 算 LAN IP 兜底。

R9 加 — 让 Dashboard 一眼看清公网/局域网访问入口,不再靠读 /tmp/tunnel_url.txt。
"""

from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

from fastapi import APIRouter, Request

from .. import _constants as C
from ..envelope import with_trace

router = APIRouter(prefix="/api", tags=["tunnel"])


def _read_url_file() -> tuple[str, str]:
    """读 tunnel_url.txt + tunnel_method.txt。失败返 ("", "")。"""
    root = Path(C.PROJECT_ROOT())
    url = ""
    method = ""
    try:
        url_file = root / "tunnel_url.txt"
        if url_file.exists():
            url = url_file.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    try:
        method_file = root / "tunnel_method.txt"
        if method_file.exists():
            method = method_file.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    # 防 QR 污染:非 http(s):// 当空
    if url and not (url.startswith("http://") or url.startswith("https://")):
        url = ""
        method = ""
    return url, method


def _lan_ip() -> str:
    """UDP socket connect 兜底拿首个非空 IP。失败返 ''。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


def _tunnel_process_alive(port: int) -> list[str]:
    """pgrep 探测后台隧道进程,排除自身。返匹配的命令行 pattern 列表。"""
    matches: list[str] = []
    patterns = [
        f"cloudflared tunnel --url",
        f"ngrok http {port}",
        f"tun_cf_client.py --port {port}",
        f"trystero_host.py --port {port}",
        f"loca.lt",
        f"ssh -tt -R 80:localhost:{port}",
        f"tailscale serve",
        f"tailscale funnel",
    ]
    for pat in patterns:
        try:
            out = subprocess.check_output(
                ["pgrep", "-f", pat], timeout=1
            ).decode().split()
            if out:
                matches.append(pat)
        except Exception:
            pass
    return matches


def _hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return ""


@router.get("/tunnel-status")
async def tunnel_status(request: Request):
    """读 tunnel_url.txt + 探测后台进程 + 算 LAN IP,聚合返回。"""
    port = C.PORT()
    url, method = _read_url_file()
    running = bool(url)
    proc_patterns: list[str] = []
    if not running:
        proc_patterns = _tunnel_process_alive(port)
        running = bool(proc_patterns)
    lan = _lan_ip()
    lan_url = f"http://{lan}:{port}" if lan else ""
    state = "online" if running else "offline"
    return with_trace(request, {
        "state": state,
        "url": url,
        "method": method,
        "lan_ip": lan,
        "lan_url": lan_url,
        "port": port,
        "hostname": _hostname(),
        "running": running,
        "process_patterns": proc_patterns,
        "ts": int(__import__("time").time() * 1000),
    })