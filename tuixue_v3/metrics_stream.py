#!/usr/bin/env python3
"""
tuixue_v3/metrics_stream.py
Ship 35/100 — 指标流 (Streaming Metrics for SSE/WS)

设计:
持续收集系统指标, 按时间窗口输出:
- per-source: 成功率 / 延迟
- per-strategy: 当前 picks 数 / IC
- per-factor: 当前 IC / 衰减
- per-portfolio: equity / drawdown

数据用环形 buffer (deque), 适合推 SSE/WebSocket

降级: 数据缺失 → 跳过, 不阻塞

2026-08-02 Ship 35 — 10000 轮迭代 P3 第十步
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class MetricPoint:
    """单个指标点"""
    name: str
    value: float
    timestamp: float
    tags: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════
# 流
# ═══════════════════════════════════════════════════════

class MetricsStream:
    """流式指标收集"""
    def __init__(self, maxlen: int = 1000):
        self.maxlen = maxlen
        self._buffer: deque = deque(maxlen=maxlen)

    def emit(self, name: str, value: float, tags: Optional[dict] = None) -> None:
        """发送一个指标点"""
        self._buffer.append(MetricPoint(
            name=name, value=value,
            timestamp=time.time(),
            tags=tags or {},
        ))

    def recent(self, name: str, n: int = 10) -> list[MetricPoint]:
        """最近 n 个同名指标"""
        result = []
        for p in reversed(self._buffer):
            if p.name == name:
                result.append(p)
                if len(result) >= n:
                    break
        return list(reversed(result))

    def latest(self, name: str) -> Optional[MetricPoint]:
        """最近一个同名指标"""
        for p in reversed(self._buffer):
            if p.name == name:
                return p
        return None

    def all_latest(self) -> dict[str, MetricPoint]:
        """所有指标的最新值"""
        seen: dict[str, MetricPoint] = {}
        for p in reversed(self._buffer):
            if p.name not in seen:
                seen[p.name] = p
        return seen

    def by_tag(self, tag_key: str, tag_value: str) -> list[MetricPoint]:
        """按 tag 过滤"""
        return [p for p in self._buffer
                if p.tags.get(tag_key) == tag_value]

    def clear(self) -> None:
        self._buffer.clear()


# ═══════════════════════════════════════════════════════
# 输出工具 (SSE 友好)
# ═══════════════════════════════════════════════════════

def to_sse(stream: MetricsStream, names: Optional[list[str]] = None) -> str:
    """转 SSE 字符串"""
    import json
    if names:
        data = [asdict(p) for p in
                [stream.latest(n) for n in names] if p]
    else:
        data = [asdict(p) for p in list(stream._buffer)[-50:]]
    return f"data: {json.dumps(data, default=str)}\n\n"


def aggregate(stream: MetricsStream, name: str,
              window_seconds: float = 60.0) -> dict:
    """聚合最近 window_seconds 的同名指标"""
    cutoff = time.time() - window_seconds
    points = [p for p in stream._buffer
              if p.name == name and p.timestamp >= cutoff]
    if not points:
        return {"name": name, "count": 0}
    values = [p.value for p in points]
    return {
        "name": name,
        "count": len(values),
        "avg": round(sum(values) / len(values), 4),
        "min": min(values),
        "max": max(values),
        "latest": values[-1],
    }
