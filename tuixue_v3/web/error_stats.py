"""
R80 (Batch 8): 错误率监控 — 5min 滚动窗口
每个端点的 (calls / errors / timeouts / error_rate) 统计
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

# {endpoint: deque[(ts, is_error, is_timeout)]}
_data: Dict[str, deque] = defaultdict(lambda: deque(maxlen=_MAX_SAMPLES_PER_KEY))
_lock = threading.Lock()


def record(endpoint: str, *, error: bool = False, timeout: bool = False) -> None:
    """记录一次端点调用结果"""
    now = time.time()
    with _lock:
        dq = _data[endpoint]
        dq.append((now, 1 if error else 0, 1 if timeout else 0))


def snapshot() -> dict:
    """返回各端点的 5min 窗口统计"""
    now = time.time()
    cutoff = now - _WINDOW_SEC
    out = {}
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
            out[ep] = {
                "calls":       calls,
                "errors":      errors,
                "timeouts":    timeouts,
                "error_rate":  round(errors / calls, 4) if calls else 0,
                "timeout_rate": round(timeouts / calls, 4) if calls else 0,
                "window_sec":  _WINDOW_SEC,
            }
    return out


def clear() -> None:
    """清空所有统计 (debug 用)"""
    with _lock:
        _data.clear()
