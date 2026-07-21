"""
R80 (Batch 8): 错误率监控 — 5min 滚动窗口
每个端点的 (calls / errors / timeouts / error_rate) 统计
R301 (2026-07-19): 加 p50/p95/p99 延迟跟踪
供 /api/_meta/error_stats 查询 + 后台告警阈值
"""
from __future__ import annotations

import time
import threading
from collections import defaultdict, deque
from typing import Dict

# R80: 5 分钟滚动窗口
_WINDOW_SEC = 300
_MAX_SAMPLES_PER_KEY = 5000
# R301: 延迟样本上限 (每个端点最多保留最近的 N 个延迟值)
_MAX_LATENCY_SAMPLES = 1000

# {endpoint: deque[(ts, is_error, is_timeout)]}
_data: Dict[str, deque] = defaultdict(lambda: deque(maxlen=_MAX_SAMPLES_PER_KEY))
# R301: {endpoint: deque[(ts, latency_ms)]} — 只存最近 latency 用于百分位计算
_latencies: Dict[str, deque] = defaultdict(lambda: deque(maxlen=_MAX_LATENCY_SAMPLES))
_lock = threading.Lock()


def record(endpoint: str, *, error: bool = False, timeout: bool = False,
           latency_ms: float | None = None) -> None:
    """记录一次端点调用结果。latency_ms 用于 p50/p95/p99 百分位计算。"""
    now = time.time()
    with _lock:
        dq = _data[endpoint]
        dq.append((now, 1 if error else 0, 1 if timeout else 0))
        if latency_ms is not None and latency_ms >= 0:
            _latencies[endpoint].append((now, latency_ms))


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """从排序列表算百分位 (0-100)。列表非空时保证返值。"""
    if not sorted_vals:
        return 0.0
    k = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(k)
    hi = lo + 1
    if hi >= len(sorted_vals):
        return sorted_vals[-1]
    return sorted_vals[lo] + (k - lo) * (sorted_vals[hi] - sorted_vals[lo])


def snapshot() -> dict:
    """返回各端点的 5min 窗口统计 + p50/p95/p99 延迟 (ms) + 进程 RSS (MB)"""
    now = time.time()
    cutoff = now - _WINDOW_SEC
    out = {}
    # 进程 RSS (MB) — Linux /proc/self/status, macOS getrusage fallback
    rss_mb = 0
    try:
        with open("/proc/self/status") as _f:
            for _line in _f:
                if _line.startswith("VmRSS:"):
                    rss_mb = int(_line.split()[1]) // 1024
                    break
    except Exception:
        try:
            import resource as _r
            rss_mb = _r.getrusage(_r.RUSAGE_SELF).ru_maxrss // 1024
        except Exception:
            pass
    with _lock:
        for ep, dq in _data.items():
            calls = errors = timeouts = 0
            for ts, err, to in dq:
                if ts < cutoff:
                    continue
                calls += 1
                if err:
                    errors += 1
                if to:
                    timeouts += 1
            if calls == 0:
                continue
            entry: dict = {
                "calls":       calls,
                "errors":      errors,
                "timeouts":    timeouts,
                "error_rate":  round(errors / calls, 4) if calls else 0,
                "timeout_rate": round(timeouts / calls, 4) if calls else 0,
                "window_sec":  _WINDOW_SEC,
            }
            # p50/p95/p99 延迟
            lat_dq = _latencies.get(ep)
            if lat_dq:
                recent_latencies = [v for ts, v in lat_dq if ts >= cutoff]
                if recent_latencies:
                    recent_latencies.sort()
                    entry["p50_ms"] = round(_percentile(recent_latencies, 50), 1)
                    entry["p95_ms"] = round(_percentile(recent_latencies, 95), 1)
                    entry["p99_ms"] = round(_percentile(recent_latencies, 99), 1)
            out[ep] = entry
    return {"endpoints": out, "rss_mb": rss_mb}


def clear() -> None:
    """清空所有统计 (debug 用)"""
    with _lock:
        _data.clear()
