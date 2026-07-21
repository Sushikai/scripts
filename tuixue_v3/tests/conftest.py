"""
tests/conftest.py — pytest harness for tuixue_v3

为 visual / contract 测试提供:
  • tuixue_server fixture — 自动起 uvicorn (lazystart), alias 已 set
  • http_client fixture — 共享 httpx.Client (复用连接)
  • reset_cache fixture — 清理 dev sqlite (cache.db + trade_reviews.db)
  • screenshots_dir fixture — /tmp/tuixue-audit/<ts>/

服务启动策略:
  - 默认假定 server 已运行 (127.0.0.1:7799)
  - 若超时,自动 fork 一个 server (PROMOTE_TUIXUE_AUTOSTART=1)
  - 否则测试用 `pytest -m "not contract"` 跳过契约类
"""
from __future__ import annotations
import os
import sys
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

import pytest

# 允许直接 `import web.server` (即使 tests/ 不在 PYTHONPATH)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOST = "127.0.0.1"
PORT = int(os.environ.get("TUIXUE_PORT", "7799"))
BASE = f"http://{HOST}:{PORT}"


def _is_listening(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_ready(base: str, timeout: float = 12.0) -> bool:
    """轮询 /api/healthz 直到 200 或超时"""
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/api/healthz", timeout=1.5) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


@pytest.fixture(scope="session")
def tuixue_server():
    """会话级 server fixture. 自动启动或假定已在跑。"""
    if _is_listening(HOST, PORT):
        yield {"url": BASE, "managed": False}
        return

    if os.environ.get("TUIXUE_AUTOSTART", "1") != "1":
        pytest.skip(f"server not listening on {BASE}")

    env = os.environ.copy()
    env.setdefault("TUIXUE_PORT", str(PORT))
    proc = subprocess.Popen(
        [sys.executable, "web/server.py"],
        cwd=str(ROOT),
        env=env,
        stdout=open("/tmp/tuixue-test-server.log", "ab", 0),
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )
    try:
        if not _wait_ready(BASE, timeout=20):
            pytest.skip(f"server start timeout; log: /tmp/tuixue-test-server.log")
        yield {"url": BASE, "managed": True, "pid": proc.pid}
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        proc.wait(timeout=4)


@pytest.fixture(scope="session")
def base_url(tuixue_server):
    return tuixue_server["url"]


@pytest.fixture
def reset_dev_cache(tmp_path):
    """把 dev cache db 复制到 tmp,跑完自动还原。视觉/规则测试隔离用。"""
    src_dir = ROOT
    backups = []
    targets = ["cache.db", "cache.db-shm", "cache.db-wal",
               "backtest_history.db", "backtest_history.db-shm", "backtest_history.db-wal"]
    for fname in targets:
        p = src_dir / fname
        if p.exists():
            bk = tmp_path / fname
            shutil.copy2(p, bk)
            backups.append((p, bk))
    yield
    for orig, bk in backups:
        if bk.exists():
            shutil.copy2(bk, orig)


@pytest.fixture
def screenshots_dir(tmp_path):
    """视觉截图落点 (每次 clean)。"""
    out = tmp_path / "screenshots"
    out.mkdir(exist_ok=True)
    return out
