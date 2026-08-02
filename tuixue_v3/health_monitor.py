#!/usr/bin/env python3
"""
tuixue_v3/health_monitor.py
Ship 28/100 — 系统健康监控

设计:
监控整个系统的健康指标:
- 数据源成功率 / 延迟
- API endpoint 响应时间
- 因子计算耗时
- 策略执行成功率
- 内存 / CPU (可选, 通过 psutil)
- 缓存命中率
- 错误率

输出:
- HealthSnapshot: 整体健康分 (0~100) + 各项指标 + 异常项

降级: 数据缺失 → 指标 = None, 不影响总分计算

2026-08-02 Ship 28 — 10000 轮迭代 P3 第三步
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class SourceHealth:
    """数据源健康度"""
    name: str
    success_count: int = 0
    fail_count: int = 0
    avg_latency_ms: float = 0.0
    last_latency_ms: float = 0.0
    last_success_ts: float = 0.0
    last_error: str = ""

    @property
    def total(self) -> int:
        return self.success_count + self.fail_count

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 1.0
        return self.success_count / self.total

    @property
    def health_score(self) -> float:
        """0~1 健康分 (成功率 × latency 罚分)"""
        sr = self.success_rate
        # latency > 5s 罚分
        latency_penalty = max(0, 1 - self.avg_latency_ms / 5000) if self.avg_latency_ms > 0 else 1.0
        return sr * 0.7 + latency_penalty * 0.3


@dataclass
class HealthSnapshot:
    """整体健康快照"""
    timestamp: float
    overall_score: float            # 0~100
    sources: dict[str, SourceHealth]
    api_p95_ms: Optional[float] = None
    cache_hit_rate: Optional[float] = None
    error_rate: Optional[float] = None
    issues: list[str] = field(default_factory=list)

    def is_critical(self) -> bool:
        return self.overall_score < 50

    def is_warning(self) -> bool:
        return 50 <= self.overall_score < 80


# ═══════════════════════════════════════════════════════
# 健康追踪器
# ═══════════════════════════════════════════════════════

class HealthTracker:
    def __init__(self, latency_window: int = 100):
        self.latency_window = latency_window
        self.sources: dict[str, SourceHealth] = {}
        self._api_latencies: deque = deque(maxlen=latency_window)
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._errors: int = 0
        self._total_requests: int = 0

    def record_source_call(self, name: str, success: bool,
                           latency_ms: float = 0.0, error: str = "") -> None:
        """记录一次数据源调用"""
        if name not in self.sources:
            self.sources[name] = SourceHealth(name=name)
        h = self.sources[name]
        if success:
            h.success_count += 1
            h.last_success_ts = time.time()
            # 更新 avg_latency (EMA)
            if h.avg_latency_ms == 0:
                h.avg_latency_ms = latency_ms
            else:
                h.avg_latency_ms = 0.8 * h.avg_latency_ms + 0.2 * latency_ms
            h.last_latency_ms = latency_ms
        else:
            h.fail_count += 1
            h.last_error = error

    def record_api_call(self, latency_ms: float, success: bool = True) -> None:
        """记录 API 调用"""
        self._api_latencies.append(latency_ms)
        self._total_requests += 1
        if not success:
            self._errors += 1

    def record_cache(self, hit: bool) -> None:
        if hit:
            self._cache_hits += 1
        else:
            self._cache_misses += 1

    def snapshot(self) -> HealthSnapshot:
        """生成健康快照"""
        issues: list[str] = []

        # 数据源平均健康分
        if self.sources:
            scores = [h.health_score for h in self.sources.values()]
            source_score = sum(scores) / len(scores)
            for name, h in self.sources.items():
                if h.health_score < 0.5:
                    issues.append(f"数据源 {name} 健康度 {h.health_score:.2f}")
        else:
            source_score = 1.0

        # API p95
        api_p95 = None
        if self._api_latencies:
            sorted_lat = sorted(self._api_latencies)
            idx = int(len(sorted_lat) * 0.95)
            api_p95 = sorted_lat[min(idx, len(sorted_lat) - 1)]
            if api_p95 > 5000:
                issues.append(f"API p95 {api_p95:.0f}ms > 5s")

        # 缓存命中率
        cache_hit_rate = None
        total_cache = self._cache_hits + self._cache_misses
        if total_cache > 0:
            cache_hit_rate = self._cache_hits / total_cache
            if cache_hit_rate < 0.5:
                issues.append(f"缓存命中率 {cache_hit_rate:.0%} < 50%")

        # 错误率
        error_rate = None
        if self._total_requests > 0:
            error_rate = self._errors / self._total_requests
            if error_rate > 0.05:
                issues.append(f"错误率 {error_rate:.1%} > 5%")

        # 综合分 (0~100)
        overall = source_score * 100
        if api_p95 is not None:
            overall -= min(20, api_p95 / 1000)  # p95 > 20s 扣 20 分
        if error_rate is not None:
            overall -= error_rate * 100  # 10% 错误率 → 扣 10 分
        overall = max(0, min(100, overall))

        return HealthSnapshot(
            timestamp=time.time(),
            overall_score=round(overall, 2),
            sources=dict(self.sources),
            api_p95_ms=round(api_p95, 2) if api_p95 else None,
            cache_hit_rate=round(cache_hit_rate, 4) if cache_hit_rate else None,
            error_rate=round(error_rate, 4) if error_rate else None,
            issues=issues,
        )


def to_dict(snapshot: HealthSnapshot) -> dict:
    return {
        "timestamp": snapshot.timestamp,
        "overall_score": snapshot.overall_score,
        "is_critical": snapshot.is_critical(),
        "is_warning": snapshot.is_warning(),
        "api_p95_ms": snapshot.api_p95_ms,
        "cache_hit_rate": snapshot.cache_hit_rate,
        "error_rate": snapshot.error_rate,
        "issues": snapshot.issues,
        "sources": {
            name: {
                "success": h.success_count, "fail": h.fail_count,
                "success_rate": round(h.success_rate, 4),
                "avg_latency_ms": round(h.avg_latency_ms, 2),
                "health_score": round(h.health_score, 4),
                "last_error": h.last_error,
            }
            for name, h in snapshot.sources.items()
        },
    }
