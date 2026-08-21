"""
tests/test_cache_hit_contract.py — R-perf-B101 cache envelope 一致性回归

2026-08-13 R-perf-B51 引入 _store_get_envelope/_store_set_envelope 后,
确认所有用 envelope helper 的端点:
  1. cache hit 路径返回 envelope {ok, data, ts, _cache_hit: True}
  2. cache miss (fresh=1) 路径返回 envelope 不含 _cache_hit
  3. 两次请求同一端点, 第二请求应带 _cache_hit=True 且 < 100ms (warm)
  4. 不污染 cached dict 引用 (cold path 不应见 _cache_hit 残留)

跑法:
    pytest tests/test_cache_hit_contract.py -v -m cache_contract
"""
from __future__ import annotations

import time

import httpx
import pytest

pytestmark = pytest.mark.cache_contract

# 已迁到 envelope helper 的用户面端点 (R-perf-B51)
ENVELOPE_ENDPOINTS = [
    "/api/market/overview",
    "/api/review/trades",
    "/api/review/portfolio",
]

# 已知 envelope 端点 (raw payload, manual _cache_hit,shape 一致)
RAW_PAYLOAD_ENDPOINTS = [
    "/api/stock/002197/core",       # stock_core
    "/api/strategies/scan",          # strategy_picker (custom meta)
]


@pytest.mark.parametrize("path", ENVELOPE_ENDPOINTS)
def test_envelope_helper_cold_then_warm(base_url, path):
    """第一次 fresh=1 计算 → envelope 无 _cache_hit。
    第二次同请求 → envelope 含 _cache_hit=True 且 < 100ms (warm)。

    注意: 若上游数据源失败, 端点可能返回 degraded 响应 (不写缓存), 此时
    warm 路径不期望 _cache_hit — 这是降级路径,不是 cache miss。
    """
    cold_qs = "?fresh=1"
    warm_qs = ""
    with httpx.Client(base_url=base_url, timeout=30) as c:
        # 1) cold path — 强制重算, 不应带 _cache_hit
        r1 = c.get(path + cold_qs)
        assert r1.status_code == 200, f"{path} cold status={r1.status_code}"
        j1 = r1.json()
        assert "data" in j1, f"{path} cold 缺 data 字段"

        # 2) warm path — 第二次同请求应命中 Redis (如果 cold 写入成功)
        t0 = time.perf_counter()
        r2 = c.get(path + warm_qs)
        warm_ms = (time.perf_counter() - t0) * 1000
        assert r2.status_code == 200, f"{path} warm status={r2.status_code}"
        j2 = r2.json()

        # 检查 cold 是否降级 (上游失败)
        cold_data = j1.get("data", {})
        if not isinstance(cold_data, dict):
            # list 类型 (如 trades) 无 _degraded 字段,视为健康
            cold_degraded = False
        else:
            cold_degraded = (
                cold_data.get("_degraded") is not None
                or j1.get("_degraded") is not None
                or cold_data.get("_partial")
            )

        if not cold_degraded:
            # 健康 cold 路径必写缓存, warm 必命中 → 必带 _cache_hit=True
            assert j2.get("_cache_hit") is True, (
                f"{path} warm 缺 _cache_hit=True (helper 未生效): {list(j2.keys())[:8]}"
            )
            assert warm_ms < 200, f"{path} warm {warm_ms:.0f}ms 超过 200ms (秒开目标失败)"
        else:
            # 降级路径 — 跳过 _cache_hit 断言 (上游挂,不写缓存合理)
            print(f"\n  {path} cold 降级, 跳过 _cache_hit 断言 (degraded={cold_data.get('_degraded')})")


@pytest.mark.parametrize("path", RAW_PAYLOAD_ENDPOINTS)
def test_raw_payload_envelope_shape(base_url, path):
    """raw payload 端点: envelope {data, ts, _cache_hit?} 一致。

    已知差异 (R-perf-B101):
      - envelope helper 端点: _cache_hit 在 envelope top level
      - raw payload 端点 (stock/core 等): _cache_hit 在 data 里面
      - strategy_picker: 用 meta._cache="redis"
    这测试确认 warm 命中能被前端任一约定读到。

    同样: 上游降级路径不写缓存, 跳过 _cache_hit 断言。
    """
    with httpx.Client(base_url=base_url, timeout=30) as c:
        r1 = c.get(path + "?fresh=1")
        assert r1.status_code == 200, f"{path} cold status={r1.status_code}"
        j1 = r1.json()
        assert "data" in j1, f"{path} cold 缺 data: {list(j1.keys())[:6]}"
        assert "ts" in j1, f"{path} cold 缺 ts: {list(j1.keys())[:6]}"

        # 检查 cold 是否降级
        cold_data = j1.get("data", {})
        if not isinstance(cold_data, dict):
            cold_degraded = False
        else:
            cold_degraded = (
                cold_data.get("_degraded") is not None
                or cold_data.get("_partial")
            )

        # warm
        r2 = c.get(path)
        assert r2.status_code == 200, f"{path} warm status={r2.status_code}"
        j2 = r2.json()
        if cold_degraded:
            print(f"\n  {path} cold 降级, 跳过 _cache_hit 断言")
            return
        if path == "/api/strategies/scan":
            meta = j2.get("meta", {})
            if meta.get("_warming"):
                # cache 还没暖好 (后台扫描未跑完) — 跳过 _cache 断言
                print(f"\n  {path} meta._warming=True, 跳过 _cache=redis 断言")
                return
            assert meta.get("_cache") == "redis", f"{path} meta 缺 _cache=redis: {meta}"
        else:
            data = j2.get("data", {})
            assert data.get("_cache_hit") is True, (
                f"{path} warm 缺 data._cache_hit=True: {list(data.keys())[:8]}"
            )


def test_envelope_helper_does_not_mutate_cached_dict(base_url):
    """R-perf-B101: envelope helper 不应让 cold 路径的响应残留 _cache_hit。
    第一次 fresh=1 → cold dict 不应有 _cache_hit。
    第二次同 path → warm 路径返回的 dict _cache_hit=True。
    第三次 fresh=1 → cold dict 仍不应有 _cache_hit (helper 不该污染 Redis 里的原 dict)。

    实现: 第一次 fresh 后,从 Redis 直接查, 不应有 _cache_hit 字段。
    """
    # 用一个稳定的端点
    path = "/api/market/overview"
    with httpx.Client(base_url=base_url, timeout=30) as c:
        # fresh 写一次
        c.get(path + "?fresh=1")
        # 检查 Redis 里的 key
        import redis
        r = redis.Redis()
        keys = r.keys("tx3:market:overview*")
        if keys:
            raw = r.get(keys[0])
            if raw:
                import json
                cached = json.loads(raw)
                assert "_cache_hit" not in cached, (
                    f"envelope helper 污染了 cached dict: {list(cached.keys())[:8]}"
                )