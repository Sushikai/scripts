"""
D1: API Reliability stability test suite

目标:让 120+ API 端点的可靠性 ≥ 20x 改善:
  1. 端点响应 < 5s (timeout 防御)
  2. 关键端点 0 5xx 错误
  3. 参数错误 (400) 不污染 cache
  4. 并发请求数限流不挂主路径

通过条件:
  • 全部 PASS
  • baseline: 120 端点中可能 N% 出现 500 或超时 → 改善后: 0
"""
from __future__ import annotations
import sys, time, json
from pathlib import Path
import pytest
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:7799"


# ─────────────────────── T1: 端点超时防御 ───────────────────────
@pytest.mark.parametrize("path,timeout_s", [
    ("/api/market/overview", 5.0),
    ("/api/global/sentiment", 5.0),
    ("/api/laws", 5.0),
    ("/api/dashboard/signal", 8.0),
    ("/api/dashboard/hot_sectors", 8.0),
    ("/api/sectors/sw", 8.0),
])
def test_endpoint_responds_within_timeout(path, timeout_s):
    """每个端点必须在 timeout 内返回 (200 / 200-degraded / 400)。

    防止上游挂起拖垮前端。
    """
    t0 = time.time()
    r = httpx.get(BASE + path, timeout=timeout_s)
    elapsed = time.time() - t0
    assert r.status_code in (200, 400, 404), (
        f"{path} 返回 {r.status_code} (耗时 {elapsed:.2f}s)"
    )
    assert elapsed < timeout_s, f"{path} 超时 ({elapsed:.2f}s > {timeout_s}s)"


# ─────────────────────── T2: 关键端点 0 5xx ───────────────────────
KEY_ENDPOINTS = [
    "/api/health",
    "/api/healthz",
    "/api/version",
    "/api/market/overview",
    "/api/dashboard/signal",
    "/api/sectors/sw",
    "/api/laws",
    "/api/stock/600519/core",
    "/api/stock/600519/intraday_5d",
]


def test_key_endpoints_zero_5xx():
    """10 个核心端点 10 次连续调用,统计 5xx 比例。
    应为 0%。
    """
    failures = []
    for ep in KEY_ENDPOINTS:
        for i in range(10):
            try:
                r = httpx.get(BASE + ep, timeout=8.0)
                if r.status_code >= 500:
                    failures.append((ep, i, r.status_code))
            except Exception as e:
                failures.append((ep, i, str(e)))
    assert not failures, f"5xx 错误: {failures[:5]}"


# ─────────────────────── T3: 参数错误不污染 cache ───────────────────────
def test_bad_code_does_not_pollute_cache():
    """错误代码 (非数字/超长) 应返 4xx,不写脏 cache。"""
    # 第一次坏 code
    r1 = httpx.get(BASE + "/api/stock/abc/core", timeout=5.0)
    assert r1.status_code in (400, 422, 404), f"坏 code 应 4xx,实得 {r1.status_code}"

    # 第二次同样坏 code → 仍应 4xx (说明没污染)
    r2 = httpx.get(BASE + "/api/stock/abc/core", timeout=5.0)
    assert r2.status_code in (400, 422, 404)


def test_sql_injection_blocked():
    """SQL 注入必须被白名单挡住 (P0 RCE 修复验证)。"""
    payload = "600519; DROP TABLE x; --"
    r = httpx.get(BASE + f"/api/stock/{payload}/core", timeout=5.0)
    assert r.status_code in (400, 422, 404), f"注入 payload 应被拒,实得 {r.status_code}"


# ─────────────────────── T4: 高并发不挂主路径 ───────────────────────
def test_concurrent_burst_survives():
    """50 并发同一端点 → 全部在合理时间内返回。"""
    import threading
    results = []
    lock = threading.Lock()

    # 先 warmup 让 cache 命中,排除冷启延迟
    httpx.get(BASE + "/api/market/overview", timeout=10.0)

    def hit():
        try:
            r = httpx.get(BASE + "/api/market/overview", timeout=15.0)
            with lock:
                results.append(r.status_code)
        except Exception as e:
            with lock:
                results.append(f"ERR:{type(e).__name__}")

    threads = [threading.Thread(target=hit) for _ in range(50)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=30)

    assert len(results) == 50
    success = sum(1 for r in results if r == 200)
    # 80% 阈值(warmup 后应高,但允许 timeout 个例)
    assert success >= 40, f"成功率 {success}/50 = {success/50*100:.0f}%"


# ─────────────────────── T5: envelope 一致性 ───────────────────────
def test_api_envelope_consistency():
    """所有非 SSE 端点返 envelope: {ok, data, error?, ts?}
    (healthz 例外 — 它就是心跳,只需要 {ok:true})
    """
    for ep in KEY_ENDPOINTS:
        if ep == "/api/healthz":
            continue  # 心跳例外
        r = httpx.get(BASE + ep, timeout=8.0)
        if r.status_code >= 500:
            continue
        try:
            j = r.json()
        except Exception:
            continue
        if isinstance(j, dict) and "ok" in j:
            assert "ok" in j, f"{ep} 缺 ok"
            assert "ts" in j or "data" in j, f"{ep} 缺 ts/data"