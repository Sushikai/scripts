#!/usr/bin/env python3
"""
tuixue_v3/metric_registry.py
Ship 40/100 — 监控指标注册中心 (Metric Registry)

设计:
统一管理指标定义:
- name, type (gauge/counter/histogram), unit, description
- 自注册: 调用 register() 后通过 name 引用
- 提供查询 (list / by_name / by_tag)
- 提供 sample (实时值)

降级: 不存在的指标返回空, 不阻塞

2026-08-03 Ship 40 — 10000 轮迭代 P3 第十五步
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 类型
# ═══════════════════════════════════════════════════════

class MetricType:
    GAUGE = "gauge"
    COUNTER = "counter"
    HISTOGRAM = "histogram"


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class MetricDef:
    name: str
    type: str
    unit: str
    description: str
    tags: dict = field(default_factory=dict)


@dataclass
class MetricSample:
    name: str
    value: float
    timestamp: float
    type: str


# ═══════════════════════════════════════════════════════
# 注册中心
# ═══════════════════════════════════════════════════════

class MetricRegistry:
    """指标注册中心 + 样本存储"""
    def __init__(self):
        self._defs: dict[str, MetricDef] = {}
        self._samples: dict[str, MetricSample] = {}
        self._lock = threading.Lock()

    # ─── 注册 ───

    def register(self, name: str, type: str = MetricType.GAUGE,
                 unit: str = "", description: str = "",
                 tags: Optional[dict] = None) -> MetricDef:
        """注册一个指标"""
        with self._lock:
            if name in self._defs:
                return self._defs[name]
            md = MetricDef(
                name=name, type=type, unit=unit,
                description=description,
                tags=tags or {},
            )
            self._defs[name] = md
            return md

    def registered(self, name: str) -> bool:
        return name in self._defs

    def all_defs(self) -> list[MetricDef]:
        return list(self._defs.values())

    def by_tag(self, key: str, value: str) -> list[MetricDef]:
        return [d for d in self._defs.values()
                if d.tags.get(key) == value]

    # ─── 采样 ───

    def record(self, name: str, value: float) -> None:
        """记录一个样本"""
        with self._lock:
            md = self._defs.get(name)
            type_ = md.type if md else MetricType.GAUGE
            self._samples[name] = MetricSample(
                name=name, value=value,
                timestamp=time.time(),
                type=type_,
            )

    def increment(self, name: str, delta: float = 1.0) -> None:
        """counter 自增"""
        with self._lock:
            md = self._defs.get(name)
            cur = self._samples.get(name)
            if cur:
                new_value = cur.value + delta
            else:
                new_value = delta
            type_ = md.type if md else MetricType.COUNTER
            self._samples[name] = MetricSample(
                name=name, value=new_value,
                timestamp=time.time(),
                type=type_,
            )

    def get(self, name: str) -> Optional[MetricSample]:
        return self._samples.get(name)

    def snapshot(self) -> list[MetricSample]:
        return list(self._samples.values())

    def by_type(self, type_: str) -> list[MetricSample]:
        return [s for s in self._samples.values() if s.type == type_]

    # ─── 输出 ───

    def export(self) -> list[dict]:
        """导出 (Prometheus 风格)"""
        out = []
        for s in self._samples.values():
            md = self._defs.get(s.name)
            out.append({
                "name": s.name,
                "type": s.type,
                "value": s.value,
                "unit": md.unit if md else "",
                "description": md.description if md else "",
                "tags": md.tags if md else {},
                "timestamp": s.timestamp,
            })
        return out

    def clear_samples(self) -> None:
        self._samples.clear()


# ═══════════════════════════════════════════════════════
# 默认注册 (全局)
# ═══════════════════════════════════════════════════════

def default_registry() -> MetricRegistry:
    """常用预注册指标"""
    r = MetricRegistry()
    r.register("latency_eastmoney", MetricType.GAUGE, "ms", "东财接口延迟", {"src": "eastmoney"})
    r.register("latency_tencent", MetricType.GAUGE, "ms", "腾讯接口延迟", {"src": "tencent"})
    r.register("latency_sina", MetricType.GAUGE, "ms", "新浪接口延迟", {"src": "sina"})
    r.register("success_eastmoney", MetricType.GAUGE, "%", "东财成功率", {"src": "eastmoney"})
    r.register("requests_total", MetricType.COUNTER, "count", "总请求数")
    r.register("errors_total", MetricType.COUNTER, "count", "总错误数")
    r.register("n_picks", MetricType.GAUGE, "count", "今日 picks")
    r.register("n_positions", MetricType.GAUGE, "count", "当前持仓")
    r.register("equity", MetricType.GAUGE, "yuan", "当前权益")
    r.register("drawdown", MetricType.GAUGE, "%", "最大回撤")
    r.register("ic_strategy_a", MetricType.GAUGE, "", "策略A IC")
    r.register("ic_strategy_b", MetricType.GAUGE, "", "策略B IC")
    return r
