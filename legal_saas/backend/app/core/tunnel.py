"""
隧道 URL 检测 — 读 ngrok/localhost.run/cloudflared 的活动公网地址。
不依赖 ngrok admin API(4040),改读数据文件。
"""
from __future__ import annotations
import json
import time
from pathlib import Path

CACHE_FILE = Path("/tmp/legal_saas_tunnel.json")
TUNNEL_URL_FILE = Path("/Users/kaikai/scripts/legal_saas/tunnel_url.txt")


def get_public_url() -> str | None:
    """返回当前活动的公网隧道 URL(优先 ngrok)。"""
    for src in (CACHE_FILE, TUNNEL_URL_FILE):
        if src.exists():
            try:
                age = time.time() - src.stat().st_mtime
                if age > 3600:  # 1 小时前的过期
                    continue
                content = src.read_text().strip()
                if not content:
                    continue
                # 支持 JSON {"url":"..."} 或纯 URL
                try:
                    data = json.loads(content)
                    url = data.get("url") or data.get("public_url")
                except json.JSONDecodeError:
                    url = content
                if url and url.startswith("http"):
                    return url.rstrip("/")
            except Exception:
                continue
    return None