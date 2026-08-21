"""
D3: Cache Consistency stability test suite

目标:让 cache_store.py 的 4 个一致性能力 ≥ 20x 提升:
  1. inflight dedup (cache stampede prevention)
  2. negative cache (空结果短 TTL)
  3. stale-while-revalidate (过期后立刻返旧值,后台刷新)
  4. key 规范化 (case/whitespace/order 不敏感)

通过条件:
  • 4 个 test_* 全部 PASS
  • baseline: 每次 1 call  → 改善后: 单 key 并发 N 次只 1 call
"""
from __future__ import annotations

import os
import sys
import threading
import time
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cache_store import CacheStore  # noqa: E402


# ─────────────────────── helpers ───────────────────────
def _make_store():
    """无 Redis 依赖:用临时 SQLite fallback + 关 Redis。"""
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    os.environ["TUIXUE_USE_REDIS"] = "0"
    s = CacheStore(fallback_db=Path(path), prefix="d3:")
    return s, Path(path)


# ─────────────────────── T1: inflight dedup ───────────────────────
def test_inflight_dedup_concurrent_same_key():
    """N 个并发 get_or_set 同一个 key → 下游 callback 只执行 1 次。

    改善目标 (vs baseline 直接 call):
      - 100 并发 → callback calls 1 (不是 100)
      - 节省 99/100 = 99% 下游开销
    """
    store, dbpath = _make_store()
    try:
        call_count = [0]
        lock = threading.Lock()

        def slow_loader(key, value):
            with lock:
                call_count[0] += 1
            time.sleep(0.05)  # 模拟 50ms IO
            return value

        results = []
        errors = []

        def worker(i):
            try:
                v = store.get_or_set(
                    "hot:key",
                    lambda: slow_loader("hot:key", f"v{i}"),
                    ttl=60,
                )
                results.append(v)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=5)

        assert not errors, f"线程异常: {errors[:3]}"
        assert len(results) == 20
        # 核心断言: callback 只应执行 1 次 (or 极少次数 — 容忍 race)
        assert call_count[0] <= 2, f"cache stampede! callback 被调用 {call_count[0]} 次"
        # 所有线程拿到的值应该一致 (single winner)
        assert len(set(results)) == 1, f"结果不一致: {set(results)}"
    finally:
        try: dbpath.unlink()
        except: pass


# ─────────────────────── T2: negative cache ───────────────────────
def test_negative_cache_none_value_cached_briefly():
    """loader 返 None → cache 一段短 TTL(默认 30s)→ 第二次不调 loader。

    改善目标: 防止"持续 miss → 持续打下游"风暴。
    """
    store, dbpath = _make_store()
    try:
        call_count = [0]

        def loader():
            call_count[0] += 1
            return None

        v1 = store.get_or_set("miss:key", loader, ttl=60, neg_ttl=2)
        v2 = store.get_or_set("miss:key", loader, ttl=60, neg_ttl=2)
        v3 = store.get_or_set("miss:key", loader, ttl=60, neg_ttl=2)

        assert v1 is None and v2 is None and v3 is None
        # 期望: loader 只调 1 次 (首次 miss 后写 negative cache)
        assert call_count[0] == 1, f"negative cache 失败: 被调 {call_count[0]} 次"
    finally:
        try: dbpath.unlink()
        except: pass


def test_negative_cache_expires_and_refetches():
    """negative TTL 过期后应能重新加载。"""
    store, dbpath = _make_store()
    try:
        call_count = [0]
        state = {"v": None}

        def loader():
            call_count[0] += 1
            return state["v"]

        v1 = store.get_or_set("miss:key", loader, ttl=60, neg_ttl=1)
        assert v1 is None
        # 等负缓存过期
        time.sleep(1.2)
        state["v"] = "now_has_value"
        v2 = store.get_or_set("miss:key", loader, ttl=60, neg_ttl=1)
        assert v2 == "now_has_value"
        assert call_count[0] == 2
    finally:
        try: dbpath.unlink()
        except: pass


# ─────────────────────── T3: stale-while-revalidate ───────────────────────
def test_stale_while_revalidate_returns_old_during_refresh():
    """TTL 过期后:立刻返旧值 + 触发后台刷新(下一次访问拿新值)。

    改善目标: TTL 边界毛刺消除 (从 1 cache miss latency → 0)。
    """
    store, dbpath = _make_store()
    try:
        # 第一次写入
        store.set("swr:key", "v1", ttl=1)
        assert store.get("swr:key") == "v1"

        time.sleep(1.1)  # 让 TTL 过期

        # 此时 get 应能返旧值(若 SWR 启用)
        v_during_refresh = store.get_swr("swr:key")
        assert v_during_refresh == "v1", (
            f"stale-while-revalidate 失败: 期望 'v1', 拿到 {v_during_refresh!r}"
        )
    finally:
        try: dbpath.unlink()
        except: pass


# ─────────────────────── T4: key normalization ───────────────────────
def test_key_normalization_case_insensitive():
    """大小写/前后空格 不应产生不同 cache entry。"""
    store, dbpath = _make_store()
    try:
        store.set_normalized("ABC", "v", ttl=60)
        # 大小写归一化:应能命中
        assert store.get_normalized("abc") == "v"
        assert store.get_normalized("ABC") == "v"
        assert store.get_normalized(" Abc ") == "v"
    finally:
        try: dbpath.unlink()
        except: pass


def test_key_normalization_strips_prefix_dup():
    """'tx3:foo' 和 'foo' 不应被当成不同 key。"""
    store, dbpath = _make_store()
    try:
        store.set_normalized("foo", "v", ttl=60)
        assert store.get_normalized("tx3:foo") == "v"
        assert store.get_normalized("foo") == "v"
        assert store.get_normalized(" TX3:FOO ") == "v"
    finally:
        try: dbpath.unlink()
        except: pass