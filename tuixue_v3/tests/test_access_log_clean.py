"""
tests/test_access_log_clean.py — access.log 5xx + 慢请求扫描

测试运行期间扫描 access.log (web/server.py 写的 JSONL):
- status >= 500 → FAIL
- latency_ms > 10000 → FAIL (除白名单: AI 端点)
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

# 白名单 (LLM 调用,允许 > 10s)
SLOW_WHITELIST = [
    "/api/watchlist",      # 首次加载缓存 20-30s
    "/api/stock_history",  # 历史同步缓存 20-30s
    "/api/ai_", "/ai_review", "/api/stock/",  # stock/{code}/ai 类
    "/api/dashboard/signal",  # 冷启动可能 30s+
]


# server 写 access.log 的位置: 启动 cwd 是 PACKAGE_PARENT (tuixue_v3 的父目录)
def _access_log_path():
    # server 启动 CWD = tuixue_v3 → access.log 与 tests/ 同级
    tests_dir = Path(__file__).resolve().parent
    pkg_dir = tests_dir.parent  # tuixue_v3/
    return pkg_dir / "access.log"


def _read_log(path):
    if not path.exists():
        return []
    out = []
    with open(path) as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


@pytest.mark.contract
def test_no_5xx_in_session(base_url):
    """本次测试期间 access.log 不能有 5xx."""
    log_path = _access_log_path()
    if not log_path.exists():
        pytest.skip(f"access.log not at {log_path}")
    size_before = log_path.stat().st_size

    # 触发一些流量
    import httpx
    with httpx.Client(base_url=base_url, timeout=15) as c:
        for path in ["/api/zt/params", "/api/zt/screener", "/api/dashboard/signal",
                     "/api/all_stocks/board"]:
            try:
                c.get(path)
            except Exception:
                pass

    time.sleep(0.5)
    with open(log_path) as f:
        f.seek(size_before)
        new = [json.loads(l) for l in f if l.strip()]

    bad = [e for e in new if e.get("status", 0) >= 500]
    assert not bad, f"access.log 出现 {len(bad)} 条 5xx:\n" + "\n".join(
        f"  {e.get('path','?')} status={e.get('status')} ms={e.get('latency_ms',0)}"
        for e in bad[:5])


@pytest.mark.contract
def test_no_slow_requests_over_10s(base_url):
    """本次测试期间 access.log 不能有 > 10s 请求 (除 AI 白名单)."""
    log_path = _access_log_path()
    if not log_path.exists():
        pytest.skip(f"access.log not at {log_path}")
    size_before = log_path.stat().st_size

    import httpx
    with httpx.Client(base_url=base_url, timeout=15) as c:
        for path in ["/api/zt/params", "/api/zt/screener", "/api/dashboard/hot_sectors"]:
            try:
                c.get(path)
            except Exception:
                pass

    time.sleep(0.5)
    with open(log_path) as f:
        f.seek(size_before)
        new = [json.loads(l) for l in f if l.strip()]

    def is_whitelisted(path):
        return any(w in path for w in SLOW_WHITELIST)

    bad = [e for e in new
           if e.get("latency_ms", 0) > 10000 and not is_whitelisted(e.get("path", ""))]
    assert not bad, f"access.log 出现 {len(bad)} 条 > 10s 请求:\n" + "\n".join(
        f"  {e.get('path','?')} ms={e.get('latency_ms',0)} status={e.get('status','?')}"
        for e in bad[:5])


@pytest.mark.contract
def test_access_log_summary(base_url):
    """access.log 汇总报告 (不 fail)."""
    log_path = _access_log_path()
    if not log_path.exists():
        pytest.skip(f"access.log not at {log_path}")
    entries = _read_log(log_path)
    if not entries:
        pytest.skip("access.log 空")

    by_path = {}
    for e in entries:
        p = e.get("path", "")
        if not p.startswith("/api/"):
            continue
        by_path.setdefault(p, []).append(e.get("latency_ms", 0))

    print(f"\n  access.log: {len(entries)} 条, {len(by_path)} 个 API 路径")
    print(f"  {'Path':<50} {'n':>5} {'p50':>8} {'p95':>8} {'max':>8} {'5xx':>4}")
    print("  " + "-" * 86)
    bad_count = 0
    for path, lats in sorted(by_path.items(), key=lambda x: -_p95(x[1])):
        lats.sort()
        n = len(lats)
        p50 = lats[n // 2]
        p95 = lats[int(n * 0.95)]
        mx = lats[-1]
        status_5xx = sum(1 for e in entries
                         if e.get("path") == path and e.get("status", 0) >= 500)
        if p95 > 200 or status_5xx:
            bad_count += 1
        print(f"  {path[:50]:<50} {n:>5} {p50:>8.0f} {p95:>8.0f} {mx:>8.0f} {status_5xx:>4}")
    print(f"\n  不达标 (p95>200 或 5xx>0): {bad_count}")


def _p95(lats):
    if not lats:
        return 0
    s = sorted(lats)
    return s[int(len(s) * 0.95)]