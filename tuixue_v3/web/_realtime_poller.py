"""
实时抓取后台 poller (2026-07-11)
- 每 30s 滚动预热 quote (东财 push2 / 腾讯 qt.gtimg)
- 数据源:自选股 + 最近 1h 访问过的股 (server 端 _recent_codes 维护)
- 目的:用户进页面时缓存多半已是新鲜的,大幅减少冷启动 5-12s 等待
- 不写日志噪声(失败 1 次自动用下次机会,30s 一次不痛)
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger("tuixue_v3.web.realtime")

# 自选股轮询周期
DEFAULT_TTL = 30  # seconds


class RealtimePoller:
    """后台守护线程,周期预热 quote 缓存。

    设计要点:
    - 单线程串行(避免打爆上游);30s 一轮,10 只股票=3s/只,留 27s 缓冲
    - 优先取"最近访问过"的前 N 只,自选股兜底
    - 抓取走 lib_common.fetch_realtime(自带多源 fallback + 重试 + 冷却)
    - 抓到的结果直接塞 _cache_quote(走 TTLCache.set),下次进页面读缓存秒开
    - 任何异常吞掉 + 不重试(下次 tick 自动再试)
    """

    def __init__(
        self,
        _recent_codes_provider: Callable[[], list[str]],
        cache_quote,                            # TTLCache 实例
        watchlist_provider: Callable[[], list[str]] | None = None,
        ttl_seconds: int = DEFAULT_TTL,
        max_codes_per_tick: int = 40,           # 一轮最多 40 只,>1k 自选也不会刷爆
    ):
        self._recent_provider = _recent_codes_provider
        self._watchlist_provider = watchlist_provider
        self._cache = cache_quote
        self.ttl_seconds = ttl_seconds
        self.max_codes = max_codes_per_tick
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # 用于"上一次刚抓过的下一轮跳过"的 cursor
        self._cursor = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="realtime-poller")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ────────────────────────────────────────────
    def _collect_codes(self) -> list[str]:
        """合code = 最近访问 ∪ 自选股,按"最近"排序,本轮最多 N 只"""
        recent = list(self._recent_provider() or [])
        watch = list(self._watchlist_provider() if self._watchlist_provider else [])
        # 自选在前(更热),最近访问在后,保留最近顺序
        seen = set()
        merged = []
        for c in watch + recent:
            if c and c not in seen and len(c) == 6 and c.isdigit():
                seen.add(c)
                merged.append(c)
        # 按"上次抓的 cursor"轮转,避免一直抓前几只
        if not merged:
            return []
        if self._cursor >= len(merged):
            self._cursor = 0
        sliced = merged[self._cursor : self._cursor + self.max_codes]
        if len(sliced) < self.max_codes:
            sliced += merged[: self.max_codes - len(sliced)]
        self._cursor = (self._cursor + len(sliced)) % len(merged)
        return sliced

    def _fetch_one(self, code: str) -> None:
        """抓一只,写入 _cache_quote。失败静默。"""
        try:
            # 必须从 web 包外调 lib_common,避免循环 import
            from .. import lib_common as lc
            q = lc.fetch_realtime(code)
            if q:
                # key 复用 server.py 里 cached() 的约定:("quote", code)
                self._cache.set(("quote", code), q)
        except Exception as e:
            log.debug(f"poller fetch {code} err: {e}")

    def _tick(self) -> None:
        codes = self._collect_codes()
        if not codes:
            return
        log.debug(f"[poller] tick: 预热 {len(codes)} 只 ({codes[:5]}...)")
        # 串行抓,避免打爆上游;每只 ≤2s 自然失败由 fetch_realtime 内部冷却接管
        for code in codes:
            if self._stop.is_set():
                return
            self._fetch_one(code)
            # 1 只 0.1s 缓冲,40 只 ≈ 4-8s,远小于 TTL
            time.sleep(0.05)

    def _loop(self) -> None:
        # 启动后先 sleep 5s,避开启动期 cache 抢占
        time.sleep(5)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                log.warning(f"[poller] tick 异常: {e}")
            # wait_for 风格 sleep — 立刻响应 stop
            if self._stop.wait(self.ttl_seconds):
                return