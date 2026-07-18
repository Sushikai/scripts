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
        max_codes_per_tick: int = 80,           # 2026-07-13 Round 13: 40→80,扩到 240/分钟覆盖
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
        # 2026-07-13 Round 13: 启动后第 1 轮把 zt_pool 全打热 — 涨停池是 all_stocks 首页必看
        # 用 5s 短间隔跑一次,后回归正常 30s 节奏
        self._warm_boost_done = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        # 2026-07-16: 启动 iTick WS 后台线程 (订阅自选股 tick, token 缺失时静默跳过)
        try:
            from . import itick_source
            itick_source.start_itick_ws_background(
                watchlist_provider=self._watchlist_provider,
            )
        except Exception as e:
            log.debug(f"itick WS 启动跳过: {e}")
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
        # R49 (Batch 5): 每轮预热 quote 完后,低频触发 seat_bd 预热 (10min 一次, 仅自选股)
        # LHB 当日不变,没必要每 30s 都跑;10min 一次足够覆盖用户进页面场景
        try:
            now = time.time()
            if (not hasattr(self, "_seat_bd_warm_last")
                    or now - self._seat_bd_warm_last > 600):
                self._seat_bd_warm_last = now
                self._warm_seat_bd(codes)
        except Exception as e:
            log.debug(f"seat_bd warm err: {e}")
        # R59 (Batch 6): 盘中 5min 一次预热 intraday L0 — 用户点分时秒开
        try:
            now = time.time()
            if (not hasattr(self, "_intraday_warm_last")
                    or now - self._intraday_warm_last > 300):
                self._intraday_warm_last = now
                self._warm_intraday_today(codes)
        except Exception as e:
            log.debug(f"intraday warm err: {e}")

    def _warm_seat_bd(self, codes: list[str]) -> None:
        """R49 (Batch 5): 后台预热 watchlist 的 seat_breakdown, 10min 一次.
        跑通 L0/L1 cache → 用户进页面时 < 1ms.
        """
        try:
            from . import seat_classify
            # 只预热前 5 只自选, 1 只 ≈ 1-10s, 别把 akshare 打爆
            for code in codes[:5]:
                if self._stop.is_set():
                    return
                try:
                    bd = seat_classify.build_breakdown(code)
                    if bd:
                        from . import server as _srv
                        # 写 L0 (本 worker) + L1 (Redis 24h)
                        _srv._cache_seat_bd.set(("seat_bd", code), bd)
                        _srv._store_set(
                            _srv.cache_store.K.SEAT_BD.format(code=code), bd, ttl=86400)
                        log.debug(f"poller seat_bd 预热 {code} OK ({len(bd.get('categories', []))} 类)")
                except Exception as e:
                    log.debug(f"poller seat_bd {code} err: {e}")
                time.sleep(0.1)
        except ImportError:
            pass

    def _warm_intraday_today(self, codes: list[str]) -> None:
        """R59 (Batch 6): 后台预热 watchlist 的今日分时 (5min 一次, 仅盘中)
        仅在 9:25-15:00 交易时段跑;非交易时段 tick 缓存 5min 后会自然过期 (盘中无效)。
        """
        try:
            from datetime import datetime
            now = datetime.now()
            # 盘中判定: 周一到周五 9:25-15:00 (粗判, 不处理节假日)
            if now.weekday() >= 5:
                return
            minute_of_day = now.hour * 60 + now.minute
            if minute_of_day < 9 * 60 + 25 or minute_of_day > 15 * 60:
                return
            from . import server as _srv
            from . import cache_store
            today = now.strftime("%Y-%m-%d")
            for code in codes[:5]:  # 只预热前 5 只
                if self._stop.is_set():
                    return
                try:
                    # 只写 L0 (60s TTL 够了 — 5min 后过期也没事, 盘中会自然刷新)
                    # L1 Redis 留 poller 之外独立处理, 这里只用 _cache_intraday
                    from . import server as _srv  # import 已在上面
                    result = _srv._fetch_intraday_for_date(code, today)
                    if result and result.get("ticks"):
                        _srv._cache_intraday.set(("intraday", code, today), result)
                        log.debug(f"poller intraday 预热 {code} OK ({len(result['ticks'])} ticks)")
                except Exception as e:
                    log.debug(f"poller intraday {code} err: {e}")
                time.sleep(0.1)
        except ImportError:
            pass

    def _loop(self) -> None:
        # 启动后先 sleep 5s,避开启动期 cache 抢占
        time.sleep(5)
        # 2026-07-13 Round 13: 启动首轮更短 (5s) — 早一点把 zt_pool 推热
        first_interval = 5
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                log.warning(f"[poller] tick 异常: {e}")
            # 第 1 轮 5s 短间隔,后回归正常
            interval = first_interval if not self._warm_boost_done else self.ttl_seconds
            self._warm_boost_done = True
            if self._stop.wait(interval):
                return