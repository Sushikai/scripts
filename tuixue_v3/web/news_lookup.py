"""
新闻模块：
- sina lid=2516 (财经要闻) + lid=2517 (7x24快讯) 双源抓取，去重合并
- 缓存 → Redis (cache_store.tx3:news:cache) TTL 动态（交易时段 90s / 非交易时段 300s），跨进程共享
- AI 分析交给 server.py 调用 MiniMax M3，结果回写到 cache.ai
- 后台 poller 每 60s 自动刷新（交易时段），非交易时段 300s
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any

import requests as _requests

log = logging.getLogger("tuixue_v3.web.news")

# 兼容旧逻辑:启动时若 Redis 没数据,从 data/news_cache.json 灌入一次
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_LEGACY_CACHE_FILE = DATA_DIR / "news_cache.json"
CACHE_TTL_TRADING = 90     # 交易时段 90s（实时）
CACHE_TTL_OFFHOURS = 300   # 非交易时段 5min（省资源）
FETCH_TIMEOUT = 5          # sina 单 lid 5s 足够
FETCH_TIMEOUT_LIVE = 4     # live poll 用更短超时，避免累积延迟
DEFAULT_NUM = 30
LIVE_NUM = 40              # live poll 多取一些保证覆盖
POLL_INTERVAL_TRADING = 60
POLL_INTERVAL_OFFHOURS = 300

# 走 cache_store (Redis 主用 + SQLite fallback)
from .. import cache_store as _cs
from ..cache_store import get_store, K
_store = get_store()

# sina 财经滚动的 lid（实测可用）
LIDS = {
    2516: "财经要闻",
    2517: "7x24快讯",
}

# A 股交易时段: 周一至周五 9:30-11:30, 13:00-15:00
_MORNING_START = dtime(9, 30)
_MORNING_END = dtime(11, 30)
_AFTERNOON_START = dtime(13, 0)
_AFTERNOON_END = dtime(15, 0)


def _is_trading_time() -> bool:
    """判断当前是否在 A 股交易时段内（含周末判断）。"""
    now = datetime.now()
    if now.weekday() >= 5:  # 周六日
        return False
    t = now.time()
    return (_MORNING_START <= t <= _MORNING_END) or (_AFTERNOON_START <= t <= _AFTERNOON_END)


def get_cache_ttl() -> int:
    """返回当前应使用的 cache TTL（秒）。"""
    return CACHE_TTL_TRADING if _is_trading_time() else CACHE_TTL_OFFHOURS


def get_poll_interval() -> int:
    """返回当前应使用的 poll 间隔（秒）。"""
    return POLL_INTERVAL_TRADING if _is_trading_time() else POLL_INTERVAL_OFFHOURS


# ── Poll lock（防多 worker 重复抓取）──────────────────────────
POLL_LOCK_KEY = "news:poll_lock"


def try_acquire_poll_lock() -> bool:
    """尝试获取 poll 锁，返回 True 表示抢到。锁 TTL 略短于 poll interval。"""
    ttl = get_poll_interval() - 5
    return _store.set_nx(POLL_LOCK_KEY, int(time.time()), ttl=ttl)


def _hash_id(title: str) -> str:
    return hashlib.md5(title.strip().encode("utf-8")).hexdigest()[:12]


def _normalize(item: dict, lid: int) -> dict | None:
    title = (item.get("title") or "").strip()
    if not title:
        return None
    ctime = int(item.get("ctime") or 0)
    intro = (item.get("intro") or "").strip()
    media = (item.get("media_name") or "").strip() or LIDS.get(lid, "")
    kws = item.get("keywords") or ""
    keywords = [k.strip() for k in kws.split(",") if k.strip()] if isinstance(kws, str) else []
    return {
        "id":      _hash_id(title),
        "title":   title,
        "url":     item.get("url") or "",
        "intro":   intro,
        "media":   media,
        "ctime":   ctime,
        "ctime_str": datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M") if ctime else "",
        "lid":     lid,
        "lid_name": LIDS.get(lid, str(lid)),
        "keywords": keywords,
    }


def _fetch_sina(lid: int, num: int = DEFAULT_NUM) -> list[dict]:
    """单 lid 抓取,带 1 次重试 (5s + 5s = 10s 上限,任一成功即可)"""
    url = f"https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid={lid}&num={num}&page=1"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.sina.com.cn/",
    }
    last_err = ""
    for attempt in range(2):
        try:
            r = _requests.get(url, timeout=FETCH_TIMEOUT, headers=headers)
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                continue
            try:
                data = r.json().get("result", {}).get("data") or []
            except Exception as e:
                last_err = f"json:{type(e).__name__}"
                continue
            out = []
            for x in data:
                n = _normalize(x, lid)
                if n:
                    out.append(n)
            if out:
                return out
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:80]}"
        # 重试前小睡
        if attempt == 0:
            import time as _t
            _t.sleep(1.0)
    log.warning(f"sina lid={lid} 失败: {last_err}")
    return []


def fetch_news(limit_per_lid: int = DEFAULT_NUM) -> list[dict]:
    """双 lid 并行抓取 + 去重 + 按时间倒序
    2026-07-11: ThreadPoolExecutor 并行两 lid, 整体 8s 预算,任一成功即返。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _one(lid: int) -> tuple[int, list[dict]]:
        return lid, _fetch_sina(lid, limit_per_lid)

    seen: dict[str, dict] = {}
    ex = ThreadPoolExecutor(max_workers=2)
    try:
        futures = {ex.submit(_one, lid): lid for lid in LIDS}
        for fut in as_completed(futures, timeout=8):
            try:
                lid, items = fut.result(timeout=1)
            except Exception as e:
                log.warning(f"sina lid={futures[fut]} 抓取异常: {e}")
                continue
            for it in items:
                if it["id"] not in seen:
                    seen[it["id"]] = it
    finally:
        ex.shutdown(wait=False)

    merged = sorted(seen.values(), key=lambda x: x.get("ctime") or 0, reverse=True)
    return merged[:limit_per_lid * 2]


# ── 缓存读写 ────────────────────────────────────────────────
_lock = threading.Lock()


def _load_cache() -> dict:
    """从 Redis 读;若空则尝试一次老 JSON 文件导入(老用户兼容)。"""
    data = _store.get(K.NEWS)
    if data and isinstance(data, dict):
        return data
    # 兼容:从老 news_cache.json 一次性灌入
    if _LEGACY_CACHE_FILE.exists():
        try:
            legacy = json.loads(_LEGACY_CACHE_FILE.read_text())
            if legacy and isinstance(legacy, dict) and legacy.get("news"):
                _store.set(K.NEWS, legacy, ttl=get_cache_ttl())
                log.info(f"已从老 news_cache.json 灌入 {len(legacy.get('news', []))} 条")
                return legacy
        except Exception as e:
            log.warning(f"老 news_cache.json 灌入失败: {e}")
    return {"fetched_at": 0, "analyzed_at": 0, "news": [], "ai": {}}


def load_cache() -> dict:
    """公开别名 — server.py 直接读 (避免触发 fetch_news)。"""
    return _load_cache()


def _save_cache(cache: dict) -> None:
    _store.set(K.NEWS, cache, ttl=get_cache_ttl())


def get_cached_news(force_refresh: bool = False, num: int = DEFAULT_NUM) -> dict:
    """
    取新闻缓存（TTL 内复用，否则刷新抓取，AI 不在抓取时强制跑）
    返回 {"fetched_at", "analyzed_at", "news": [...], "ai": {id: {...}}, "_stale": bool}

    TTL 动态: 交易时段 90s / 非交易时段 300s。
    若 fetch_news 失败且旧 cache 非空,标记 _stale=True 返旧 cache。
    """
    ttl = get_cache_ttl()
    with _lock:
        cache = _load_cache()
        age = time.time() - (cache.get("fetched_at") or 0)
        if not force_refresh and age < ttl and cache.get("news"):
            log.info(f"news 缓存命中 (age={age:.0f}s, ttl={ttl}s)")
            return cache

        log.info(f"news 抓取刷新 (age={age:.0f}s, ttl={ttl}s, force={force_refresh})")
        news = fetch_news(num)
        if not news and cache.get("news"):
            # 抓取失败 → 保留旧 cache, 标 stale
            log.warning("news 抓取失败,降级返 stale cache")
            cache["_stale"] = True
            return cache

        cache["news"] = news
        cache["fetched_at"] = int(time.time())
        cache["_stale"] = False
        # 保留旧 ai 中仍然存在的 id；删除已下榜的
        alive_ids = {n["id"] for n in news}
        cache["ai"] = {k: v for k, v in (cache.get("ai") or {}).items() if k in alive_ids}
        if not cache.get("analyzed_at"):
            cache["analyzed_at"] = 0
        _save_cache(cache)
        return cache


def fetch_live_news() -> dict:
    """
    轻量版实时抓取：更短超时，更多条数，用于后台 poller。
    调用 get_cached_news 但 force_refresh=True，返回完整 cache dict。
    与 get_cached_news 共享同一把 _lock，不会并发抓取。
    """
    return get_cached_news(force_refresh=True, num=LIVE_NUM)


def save_ai_analysis(ai_map: dict[str, dict]) -> None:
    """把 AI 评分合并进缓存（保留已有）"""
    with _lock:
        cache = _load_cache()
        cache["ai"] = {**(cache.get("ai") or {}), **ai_map}
        cache["analyzed_at"] = int(time.time())
        _save_cache(cache)