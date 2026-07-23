"""flow 测试 fixture:启动 uvicorn 子进程,提供 httpx client。"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_http(url: str, timeout: float = 30.0) -> bool:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        try:
            r = httpx.get(url, timeout=1.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


@pytest.fixture(scope="session")
def flow_server() -> dict:
    """启动一个 flow uvicorn 子进程,跑 session 期间。"""
    port = _free_port()
    env = os.environ.copy()
    env["FLOW_PORT"] = str(port)
    env["FLOW_DB"] = str(ROOT / "data" / "test_flow.db")
    env["FLOW_CACHE_DB"] = str(ROOT / "data" / "test_cache.db")
    env["FLOW_ACCESS_LOG"] = str(ROOT / "data" / "test_access.log")
    env["FLOW_RATE_LIMIT_DEFAULT"] = "100000"
    env["FLOW_RATE_LIMIT_AI"] = "100000"
    env["FLOW_RATE_LIMIT_JOB"] = "100000"
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    (ROOT / "data").mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        if not _wait_http(base + "/health", timeout=30.0):
            proc.terminate()
            raise RuntimeError(f"flow server failed to start on {base}")
        yield {"base": base, "port": port, "proc": proc}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def client(flow_server) -> httpx.Client:
    # 每个 test 重置限频,避免跨测试累积
    import sys
    sys.path.insert(0, str(ROOT))
    from backend.middleware.rate_limit import reset_for_tests
    reset_for_tests()
    with httpx.Client(base_url=flow_server["base"], timeout=10.0) as c:
        yield c


@pytest.fixture(scope="session")
def browser():
    """Playwright browser,session 级共享。"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()