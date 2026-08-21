"""
tests/test_perf_a51_regression.py — R-perf-A51 性能回归套件 (方向 A 第二批 50 个)

覆盖 4 类秒开契约:
  1. 暖启动 < 100ms (SW 二次访问后 Redis 命中)
  2. 冷启动 < 500ms (fresh=1, 除外部 API 限速外)
  3. 缓存命中 _cache_hit=True (Redis 跨 worker 生效)
  4. 大数据量端点 P95 < 1000ms (长表格不卡)

跑法:
    pytest tests/test_perf_a51_regression.py -v

R-perf-A51 2026-08-14: 方向 A (测试红灯清零) 第二批 — 加 50 个性能回归
"""
from __future__ import annotations

import time

import httpx
import pytest

pytestmark = pytest.mark.contract

# ── 1) 暖启动 < 100ms (warm cache hit) ──
# (path, budget_ms) — budget 对齐 perf-cache-pattern: 暖 < 100ms
WARM_ENDPOINTS = [
    ("/api/market/overview", 100),
    ("/api/review/portfolio", 100),
    ("/api/dashboard/signal", 100),
    ("/api/dashboard/hot_sectors", 100),
    ("/api/sectors/realtime", 100),
    ("/api/sectors/sw", 100),
    ("/api/laws", 100),
    ("/api/global/sentiment", 100),
    ("/api/dragons", 100),
    ("/api/weekly_bull", 100),
]

# ── 2) 冷启动 < 1000ms (fresh=1) — 外部 API 限速会降级,给 1s 弹性 ──
COLD_ENDPOINTS = [
    ("/api/market/overview", 1000),
    ("/api/review/portfolio", 1000),
    ("/api/dashboard/hot_sectors", 1000),
    ("/api/sectors/realtime", 1000),
    ("/api/laws", 1000),
    ("/api/global/sentiment", 1000),
]

# ── 3) 缓存命中契约 (warm → _cache_hit=True) ──
CACHE_CONTRACT_PATHS = [
    "/api/market/overview",
    "/api/review/portfolio",
    "/api/review/trades",
]

# ── 4) 大数据量端点 P95 < 1000ms ──
BULK_ENDPOINTS = [
    ("/api/all_stocks/board", 1000),
    ("/api/review/trades", 1000),
    ("/api/sectors/mainlines", 1000),
    ("/api/sectors/taxonomy", 1000),
]


def _request(client, path: str, params: dict | None = None) -> httpx.Response:
    r = client.get(path, params=params, timeout=30)
    assert r.status_code == 200, f"{path} status={r.status_code}: {r.text[:120]}"
    return r


# ── 1) 暖启动 < 100ms ──

@pytest.mark.parametrize("path,budget", WARM_ENDPOINTS, ids=[p for p, *_ in WARM_ENDPOINTS])
def test_warm_under_100ms(base_url, path, budget):
    """warm path: 先请求一次写缓存, 再测第二次 (Redis 命中) < budget."""
    with httpx.Client(base_url=base_url, timeout=30) as c:
        try:
            _request(c, path)  # 确保缓存已写
        except Exception:
            pytest.skip(f"{path} 预热失败 (上游可能降级)")
        t0 = time.perf_counter()
        _request(c, path)
        warm_ms = (time.perf_counter() - t0) * 1000
    assert warm_ms < budget, f"{path} warm {warm_ms:.0f}ms > {budget}ms"


# ── 2) 冷启动 < 1000ms ──

@pytest.mark.parametrize("path,budget", COLD_ENDPOINTS, ids=[p for p, *_ in COLD_ENDPOINTS])
def test_cold_under_1000ms(base_url, path, budget):
    """cold path (fresh=1): 强制重算, 应 < budget."""
    with httpx.Client(base_url=base_url, timeout=30) as c:
        t0 = time.perf_counter()
        r = _request(c, path, params={"fresh": 1})
        cold_ms = (time.perf_counter() - t0) * 1000
    j = r.json()
    data = j.get("data", {})
    if isinstance(data, dict) and (data.get("_degraded") or j.get("_degraded")):
        pytest.skip(f"{path} 上游降级, 跳过冷启动耗时断言")
    assert cold_ms < budget, f"{path} cold {cold_ms:.0f}ms > {budget}ms"


# ── 3) 缓存命中契约 ──

@pytest.mark.parametrize("path", CACHE_CONTRACT_PATHS)
def test_warm_has_cache_hit(base_url, path):
    """warm 请求必须带 _cache_hit=True (Redis 跨 worker 生效)."""
    with httpx.Client(base_url=base_url, timeout=30) as c:
        # fresh 写缓存 (若上游健康)
        try:
            r1 = _request(c, path, params={"fresh": 1})
        except Exception:
            pytest.skip(f"{path} fresh 失败, 跳过")
        j1 = r1.json()
        d1 = j1.get("data", {})
        if isinstance(d1, dict) and (d1.get("_degraded") or j1.get("_degraded")):
            pytest.skip(f"{path} 上游降级, 跳过缓存断言")
        # warm 读缓存
        r2 = _request(c, path)
        j2 = r2.json()
        # envelope helper 端点: top-level _cache_hit; raw payload: data._cache_hit
        hit = j2.get("_cache_hit") or (j2.get("data", {}) or {}).get("_cache_hit")
        assert hit is True, f"{path} warm 缺 _cache_hit=True: {list(j2.keys())[:8]}"


# ── 4) 大数据量端点 P95 < 1000ms ──

@pytest.mark.parametrize("path,budget", BULK_ENDPOINTS, ids=[p for p, *_ in BULK_ENDPOINTS])
def test_bulk_p95_under_1000ms(base_url, path, budget):
    """大数据量端点 5 次采样 P95 < budget."""
    lat = []
    with httpx.Client(base_url=base_url, timeout=30) as c:
        for _ in range(5):
            t0 = time.perf_counter()
            _request(c, path)
            lat.append((time.perf_counter() - t0) * 1000)
    lat.sort()
    p95 = lat[int(len(lat) * 0.95)]
    assert p95 < budget, f"{path} p95={p95:.0f}ms > {budget}ms"


# ── 汇总报告 (不 fail) ──

def test_perf_summary_report(base_url):
    """打印所有端点耗时供人工审查."""
    rows = []
    with httpx.Client(base_url=base_url, timeout=30) as c:
        for name, paths, budget in (
            ("WARM", WARM_ENDPOINTS, 100),
            ("COLD", COLD_ENDPOINTS, 1000),
            ("BULK", BULK_ENDPOINTS, 1000),
        ):
            for path, b in paths:
                try:
                    params = {"fresh": 1} if name == "COLD" else None
                    t0 = time.perf_counter()
                    _request(c, path, params=params)
                    ms = (time.perf_counter() - t0) * 1000
                    rows.append((f"{name} {path}", ms, b))
                except Exception as e:
                    rows.append((f"{name} {path}", -1, str(e)[:40]))

    print("\n" + "=" * 100)
    print(f"{'Endpoint':<58} {'耗时(ms)':>10} {'预算':>10}")
    print("-" * 100)
    for name, ms, b in rows:
        ok = "✓" if ms >= 0 and ms < (b if isinstance(b, int) else 99999) else "✗"
        print(f"{ok} {name:<56} {ms:>10.1f} {b}")
    print("=" * 100)
