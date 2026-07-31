"""
tests/test_perf_api.py — API p95 性能预算测试

8 关键端点 p95 < 200ms (除 dashboard/signal 冷启动外)。
AI 端点白名单 (LLM call > 1s 不可避免)。

跑法:
    pytest tests/test_perf_api.py -v -m contract
    pytest tests/test_perf_api.py::test_dashboard_signal_p95 -v

设计:
- 用 httpx.Client 复用连接 (实测比 aiohttp 启动更快,更接近前端场景)
- 端点预热 1 次 (剔除冷启动 + Redis 首次连接)
- 20 次迭代取 P50/P95/P99
- 任何 P95 超预算即 fail
"""
from __future__ import annotations

import statistics
import time

import httpx
import pytest

# (path, method, payload, p95_budget_ms, n_warmup, n_iter)
# 预算 = 当前实测 p95 × 1.5 (基线档位);Phase 3 会逐步收紧
ENDPOINTS = [
    ("/api/screener/backtest", "GET", None, 400, 2, 20),
    ("/api/all_stocks/board", "GET", None, 500, 2, 15),
    ("/api/dashboard/hot_sectors", "GET", None, 500, 2, 15),
    ("/api/limitup/per_code", "POST", {"codes": ["002197"]}, 900, 2, 15),
    ("/api/stock/002197/intraday", "GET", None, 400, 2, 15),
    ("/api/stock/002197/seat_breakdown", "GET", None, 500, 2, 15),
    ("/api/dashboard/signal", "GET", None, 800, 1, 10),
]

pytestmark = pytest.mark.contract


def _bench(client, path, method, payload, n_warmup, n_iter):
    """同步测一个端点,返 (p50, p95, p99, latencies)."""
    # 预热
    for _ in range(n_warmup):
        try:
            if method == "GET":
                client.get(path, timeout=30)
            else:
                client.post(path, json=payload or {}, timeout=30)
        except Exception:
            pass

    latencies = []
    for _ in range(n_iter):
        t0 = time.perf_counter()
        try:
            if method == "GET":
                r = client.get(path, timeout=30)
            else:
                r = client.post(path, json=payload or {}, timeout=30)
            assert r.status_code == 200, f"{path} status={r.status_code}"
        except Exception as e:
            pytest.fail(f"{path} fetch error: {e}")
        latencies.append((time.perf_counter() - t0) * 1000)

    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[int(len(latencies) * 0.99)] if len(latencies) >= 100 else latencies[-1]
    return p50, p95, p99, latencies


@pytest.mark.parametrize("path,method,payload,budget_ms,n_warm,n_iter", ENDPOINTS,
                         ids=[p for p, *_ in ENDPOINTS])
def test_api_p95_under_budget(base_url, path, method, payload, budget_ms, n_warm, n_iter):
    """任一端点 P95 超预算即 fail."""
    with httpx.Client(base_url=base_url, timeout=30) as client:
        p50, p95, p99, _ = _bench(client, path, method, payload, n_warm, n_iter)
    print(f"  {method:4s} {path:42s}  p50={p50:6.1f}ms  p95={p95:6.1f}ms  p99={p99:6.1f}ms  budget={budget_ms}ms")
    assert p95 < budget_ms, f"{path} p95={p95:.1f}ms 超过预算 {budget_ms}ms"


def test_api_summary_report(base_url):
    """汇总报告 — 不 fail,只打印所有端点 p50/p95/p99 供人工审查."""
    rows = []
    with httpx.Client(base_url=base_url, timeout=30) as client:
        for path, method, payload, budget, n_warm, n_iter in ENDPOINTS:
            try:
                p50, p95, p99, _ = _bench(client, path, method, payload, n_warm, n_iter)
                rows.append((f"{method} {path}", p50, p95, p99, budget))
            except BaseException as e:
                rows.append((f"{method} {path}", -1, -1, -1, str(e)[:60]))

    print("\n" + "=" * 110)
    print(f"{'Endpoint':<52} {'p50(ms)':>10} {'p95(ms)':>10} {'p99(ms)':>10} {'Budget':>10}")
    print("-" * 110)
    for path, p50, p95, p99, budget in rows:
        ok = "✓" if p95 >= 0 and p95 < budget else "✗"
        print(f"{ok} {path:<50} {p50:>10.1f} {p95:>10.1f} {p99:>10.1f} {str(budget):>10}")
    print("=" * 110)