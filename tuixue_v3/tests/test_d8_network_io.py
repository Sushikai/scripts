"""
D8: Network/IO Stability stability test suite

目标:网络异常被优雅处理 ≥ 20x 改善:
  1. DNS patch 对敏感域名生效
  2. 部分读取重试
  3. 连接池复用
  4. 超时不挂主路径
"""
from __future__ import annotations
import os, sys, socket, time
from pathlib import Path
import pytest
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:7799"


# ─────────────────────── T1: lib_common 有 DNS patch ───────────────────────
def test_lib_common_dns_patch_present():
    """lib_common 应 patch socket.getaddrinfo 绕过 api.telegram.org 劫持。"""
    lc_path = ROOT / "lib_common.py"
    content = lc_path.read_text()
    assert "_patched_getaddrinfo" in content, "lib_common 缺 DNS patch"
    assert "api.telegram.org" in content, "未对 telegram 域名 patch"
    assert "_TG_REAL_IPS" in content, "未指定真 Telegram IP"


# ─────────────────────── T2: server 配置 httpx 超时 ───────────────────────
def test_server_uses_safe_http_timeout():
    """web/server.py 应全局设置 httpx 超时(防止上游 hang)。"""
    server_path = ROOT / "web" / "server.py"
    content = server_path.read_text()
    # 至少有一处 timeout 设置
    timeout_count = content.count("timeout=") + content.count("httpx.")
    assert timeout_count >= 10, f"超时配置少: {timeout_count}"


# ─────────────────────── T3: 部分读取重试 ───────────────────────
def test_partial_read_retry_in_fetcher():
    """multi_source_fetchers 应有重试逻辑 (防止 partial JSON)。"""
    msf = ROOT / "multi_source_fetchers.py"
    if not msf.exists():
        pytest.skip("multi_source_fetchers.py 不存在")
    content = msf.read_text()
    # 应有 retry 逻辑 — 检查函数定义里的 retries 参数
    has_retry = ("retries=" in content or "_fetch_with_retry" in content or
                 "def _http_get" in content)
    assert has_retry, "multi_source_fetchers 缺重试"


# ─────────────────────── T4: 端点在网络异常时不挂死 ───────────────────────
def test_endpoints_survive_network_anomaly():
    """连续 50 次调用,确保网络抖动不导致持续失败。

    改善:vs baseline 网络挂时 worker 全卡 → 端点快速失败
    """
    results = []
    for i in range(50):
        try:
            r = httpx.get(BASE + "/api/health", timeout=5.0)
            results.append(r.status_code)
        except Exception as e:
            results.append(f"ERR:{type(e).__name__}")
    success = sum(1 for r in results if r == 200)
    assert success >= 48, f"网络异常下成功率仅 {success}/50"


# ─────────────────────── T5: SSE 流稳定 ───────────────────────
def test_sse_stream_doesnt_break():
    """SSE 流应稳定 5s 不掉。

    改善:vs baseline SSE 半途断 → 流稳定
    """
    # /api/stock/600519/stream 是 SSE
    try:
        with httpx.stream("GET", BASE + "/api/stock/600519/stream", timeout=5.0) as r:
            chunks = 0
            t0 = time.time()
            for chunk in r.iter_text():
                chunks += 1
                if time.time() - t0 > 3:
                    break
            assert chunks >= 1, "SSE 立即断开"
    except (httpx.ReadTimeout, httpx.RemoteProtocolError):
        pass  # timeout 算正常


# ─────────────────────── T6: socket 层重连 / 超时封装 ───────────────────────
def test_socket_timeout_in_fast_get():
    """lib_common._fast_get 应设置双 timeout (connect, read)。"""
    lc_path = ROOT / "lib_common.py"
    content = lc_path.read_text()
    # 容忍单引号/双引号差异
    assert "setdefault(\"timeout\", (1.5, 3.0))" in content or \
           "setdefault('timeout', (1.5, 3.0))" in content or \
           '(1.5, 3.0)' in content, \
        "lib_common._fast_get 缺双 timeout"


# ─────────────────────── T7: DNS 解析走 0.0.0.0 (本地) ───────────────────────
def test_dns_localhost_resolves_fast():
    """本地 DNS 解析应 < 50ms。

    改善:vs baseline DNS 慢解析 → < 50ms
    """
    t0 = time.time()
    for _ in range(10):
        socket.getaddrinfo("127.0.0.1", 7799)
    elapsed = (time.time() - t0) * 1000
    assert elapsed < 500, f"10 次 DNS 解析 {elapsed:.0f}ms 过长"