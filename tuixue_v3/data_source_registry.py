#!/usr/bin/env python3
"""
tuixue_v3/data_source_registry.py
统一数据源注册器 (FetchRegistry) — Ship 1 of 10000

设计目标:
1) 把 30+ 个 fetch 调用统一到单一注册表 (现有 _DAILY_SOURCES / _REALTIME_SOURCES 改造)
2) 新数据源接入只需 1 个 register 调用,自动接入所有端点
3) 自动 fallback (Top N 并行竞速 + 串行兜底) + 健康监控透传
4) 接口契约标准化 (name/code/timeout/category/owner/schema_version)
5) 不破坏现有调用 — 提供 `register()` + `fetch()` API,老路径逐步迁移

使用示例:
    from tuixue_v3.data_source_registry import registry, fetch_with_registry
    @registry.register("tushare_daily", category="daily", timeout=8.0, owner="@kai")
    def _tushare_daily(code, days=120):
        return ts.pro_bar(...)

    df, src = fetch_with_registry("daily", code="600519", days=120)

2026-08-02 Ship 1 — 10000 轮迭代起点
"""

from __future__ import annotations

import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 核心数据类 — 注册器契约
# ═══════════════════════════════════════════════════════

@dataclass
class FetchSource:
    """单个数据源注册项 — 不可变,注册后只能 disable/enable"""
    name: str                                  # 内部唯一标识 (e.g. "tencent_qq")
    category: str                              # daily / realtime / intraday / fund_flow / news / ...
    fn: Callable[..., Any]                     # fetch 函数 (code, **kwargs) -> data
    display_name: str = ""                     # UI 显示名 (e.g. "腾讯 qt.gtimg")
    timeout: float = 4.0                       # 单源超时 (秒)
    priority: int = 100                        # 数值越小优先级越高,Top N 并行竞速用
    owner: str = ""                            # 负责人/团队 (e.g. "@kai")
    requires: Optional[Callable[[Any], bool]] = None  # 数据校验函数,返回 True 表示数据有效
    enabled: bool = True                       # 管理员可临时禁用
    schema_version: str = "v1"                 # 字段契约版本 (防字段漂移)
    tags: List[str] = field(default_factory=list)  # e.g. ["free", "rate-limited", "auth-required"]
    registered_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if not self.display_name:
            self.display_name = self.name


@dataclass
class FetchResult:
    """统一 fetch 返回 — 包含数据 + 来源 + 元信息,方便上层做容错和监控"""
    data: Any                                  # fetch 到的数据 (类型因 category 而异)
    source: str                                # 实际成功的源 name
    elapsed_ms: float                          # 总耗时 (含 fallback)
    attempts: int                              # 尝试的源数量
    fallback_chain: List[str] = field(default_factory=list)  # 实际尝试顺序


# ═══════════════════════════════════════════════════════
# Registry 主类 — 线程安全
# ═══════════════════════════════════════════════════════

class FetchRegistry:
    """全局数据源注册器 — 单例模式"""

    def __init__(self):
        self._sources: Dict[str, FetchSource] = {}
        self._by_category: Dict[str, List[str]] = {}
        self._lock = threading.RLock()

    # ── 注册 ──
    def register(self, source: FetchSource) -> FetchSource:
        """注册一个数据源(线程安全)"""
        with self._lock:
            if source.name in self._sources:
                logger.warning(f"数据源 [{source.name}] 已存在,覆盖")
            self._sources[source.name] = source
            self._by_category.setdefault(source.category, [])
            if source.name not in self._by_category[source.category]:
                self._by_category[source.category].append(source.name)
            logger.debug(
                f"注册数据源: {source.name} ({source.category}, "
                f"priority={source.priority}, timeout={source.timeout}s)"
            )
            return source

    def register_fn(
        self,
        name: str,
        category: str,
        fn: Callable,
        display_name: Optional[str] = None,
        timeout: float = 4.0,
        priority: int = 100,
        owner: str = "",
        requires: Optional[Callable[[Any], bool]] = None,
        schema_version: str = "v1",
        tags: Optional[List[str]] = None,
    ) -> FetchSource:
        """便捷函数式注册 (装饰器用)"""
        src = FetchSource(
            name=name,
            display_name=display_name or name,
            category=category,
            fn=fn,
            timeout=timeout,
            priority=priority,
            owner=owner,
            requires=requires,
            schema_version=schema_version,
            tags=tags or [],
        )
        return self.register(src)

    # ── 查询 ──
    def get(self, name: str) -> Optional[FetchSource]:
        with self._lock:
            return self._sources.get(name)

    def list_by_category(self, category: str) -> List[FetchSource]:
        """返回某 category 下所有源 (按 priority 排序)"""
        with self._lock:
            names = self._by_category.get(category, [])
            return sorted(
                (self._sources[n] for n in names if self._sources[n].enabled),
                key=lambda s: s.priority,
            )

    def list_all(self) -> List[FetchSource]:
        with self._lock:
            return list(self._sources.values())

    def categories(self) -> List[str]:
        with self._lock:
            return list(self._by_category.keys())

    # ── 管理 ──
    def enable(self, name: str):
        with self._lock:
            if name in self._sources:
                self._sources[name].enabled = True

    def disable(self, name: str):
        with self._lock:
            if name in self._sources:
                self._sources[name].enabled = False
                logger.info(f"数据源 [{name}] 已被手动禁用")

    def stats(self) -> Dict[str, Any]:
        """供 /api/sources/health 使用"""
        with self._lock:
            by_cat: Dict[str, int] = {}
            enabled = 0
            disabled = 0
            for s in self._sources.values():
                by_cat[s.category] = by_cat.get(s.category, 0) + 1
                if s.enabled:
                    enabled += 1
                else:
                    disabled += 1
            return {
                "total": len(self._sources),
                "enabled": enabled,
                "disabled": disabled,
                "by_category": by_cat,
            }


# 全局单例
registry = FetchRegistry()


# ═══════════════════════════════════════════════════════
# 统一 fetch 入口
# ═══════════════════════════════════════════════════════

def fetch_with_registry(
    category: str,
    code: str,
    *,
    race_top_n: int = 3,
    race_timeout: float = 4.0,
    fallback_timeout: float = 2.0,
    **kwargs,
) -> FetchResult:
    """
    统一 fetch 入口 — Top N 并行竞速 + 串行兜底

    Args:
        category: 数据类别 (daily / realtime / intraday / ...)
        code: 股票代码
        race_top_n: 并行竞速的源数量 (默认 3)
        race_timeout: 并行竞速单源超时 (秒)
        fallback_timeout: 串行兜底单源超时 (秒)
        **kwargs: 透传给 fetch 函数 (e.g. days=120)

    Returns:
        FetchResult: data + source + elapsed_ms + attempts
    """
    t0 = time.monotonic()
    sources = registry.list_by_category(category)
    if not sources:
        logger.warning(f"category=[{category}] 无可用数据源")
        return FetchResult(data=None, source="", elapsed_ms=0, attempts=0)

    fallback_chain: List[str] = []

    # 阶段 1: Top N 并行竞速
    top = sources[:race_top_n]
    fallback_chain.extend(s.name for s in top)
    data, src = _race_top_n(top, code, race_timeout, **kwargs)
    if data is not None:
        elapsed = (time.monotonic() - t0) * 1000
        return FetchResult(
            data=data, source=src, elapsed_ms=elapsed,
            attempts=len(fallback_chain), fallback_chain=fallback_chain,
        )

    # 阶段 2: 串行兜底剩余源
    rest = sources[race_top_n:]
    for s in rest:
        fallback_chain.append(s.name)
        data = _try_one(s, code, fallback_timeout, **kwargs)
        if data is not None:
            elapsed = (time.monotonic() - t0) * 1000
            return FetchResult(
                data=data, source=s.name, elapsed_ms=elapsed,
                attempts=len(fallback_chain), fallback_chain=fallback_chain,
            )

    elapsed = (time.monotonic() - t0) * 1000
    logger.warning(
        f"category=[{category}] code=[{code}] 全部源失败 ({len(fallback_chain)} 个尝试)"
    )
    return FetchResult(
        data=None, source="", elapsed_ms=elapsed,
        attempts=len(fallback_chain), fallback_chain=fallback_chain,
    )


def _race_top_n(
    sources: List[FetchSource],
    code: str,
    timeout: float,
    **kwargs,
) -> Tuple[Any, str]:
    """并行竞速: Top N 源同时请求,首个返回有效数据的胜出"""
    if not sources:
        return None, ""
    ex = ThreadPoolExecutor(max_workers=len(sources))
    try:
        future_to_src = {
            ex.submit(_try_one, s, code, timeout, **kwargs): s.name
            for s in sources
        }
        for fut in as_completed(future_to_src, timeout=timeout + 1.0):
            src_name = future_to_src[fut]
            try:
                data = fut.result()
                if data is not None:
                    return data, src_name
            except Exception as e:
                logger.debug(f"竞速源 [{src_name}] 异常: {e}")
        return None, ""
    finally:
        ex.shutdown(wait=False)


def _try_one(source: FetchSource, code: str, timeout: float, **kwargs) -> Any:
    """单源 fetch + 校验 + 异常捕获"""
    if not source.enabled:
        return None
    try:
        # 用线程做硬超时 (防止 fn 自身不响应)
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            fut = ex.submit(source.fn, code, **kwargs)
            data = fut.result(timeout=timeout)
        finally:
            ex.shutdown(wait=False)
        if data is None:
            return None
        if source.requires and not source.requires(data):
            logger.debug(f"源 [{source.name}] 返回数据未通过 requires 校验")
            return None
        return data
    except Exception as e:
        logger.debug(f"源 [{source.name}] 异常: {type(e).__name__}: {e}")
        return None


# ═══════════════════════════════════════════════════════
# 装饰器: 一键注册 (兼容旧 fn 签名)
# ═══════════════════════════════════════════════════════

def register_source(
    name: str,
    category: str,
    display_name: Optional[str] = None,
    timeout: float = 4.0,
    priority: int = 100,
    owner: str = "",
    requires: Optional[Callable[[Any], bool]] = None,
    schema_version: str = "v1",
    tags: Optional[List[str]] = None,
):
    """装饰器: 把 fetch 函数注册到全局 registry

    Example:
        @register_source("tushare_daily", category="daily", priority=15)
        def _tushare_daily(code, days=120):
            return ts.pro_bar(ts_code=f"{code}", adj='qfq', start_date=...)
    """
    def decorator(fn):
        registry.register_fn(
            name=name, category=category, fn=fn,
            display_name=display_name, timeout=timeout, priority=priority,
            owner=owner, requires=requires, schema_version=schema_version, tags=tags,
        )
        return fn
    return decorator


# ═══════════════════════════════════════════════════════
# 接入现有 _DAILY_SOURCES / _REALTIME_SOURCES (兼容层)
# ═══════════════════════════════════════════════════════

def bootstrap_from_legacy():
    """把 lib_common._DAILY_SOURCES / _REALTIME_SOURCES 接入 registry

    增量接入:不破坏老调用,仅在 registry 建索引。后续 ship 逐步替换 fetch 调用。
    """
    try:
        from tuixue_v3 import lib_common as lc
    except ImportError:
        logger.debug("lib_common 未找到,跳过 legacy bootstrap")
        return

    # 日线源
    for i, (name, fn) in enumerate(getattr(lc, "_DAILY_SOURCES", [])):
        if registry.get(name):
            continue
        registry.register_fn(
            name=name, category="daily", fn=fn,
            display_name=name, timeout=4.0, priority=i * 10 + 10,
            owner="@legacy", requires=lc._require_kline,
            schema_version="v1", tags=["legacy"],
        )

    # 实时源
    for i, (name, fn) in enumerate(getattr(lc, "_REALTIME_SOURCES", [])):
        if registry.get(name):
            continue
        registry.register_fn(
            name=name, category="realtime", fn=fn,
            display_name=name, timeout=4.0, priority=i * 10 + 10,
            owner="@legacy", requires=lc._require_realtime_quote,
            schema_version="v1", tags=["legacy"],
        )

    logger.info(
        f"Legacy bootstrap 完成: "
        f"daily={len(getattr(lc, '_DAILY_SOURCES', []))}, "
        f"realtime={len(getattr(lc, '_REALTIME_SOURCES', []))}, "
        f"registry 总源={len(registry.list_all())}"
    )


# 模块导入时自动 bootstrap (容错:lib_common 不存在也不挂)
try:
    bootstrap_from_legacy()
except Exception as e:
    logger.debug(f"bootstrap_from_legacy 失败 (非阻塞): {e}")
