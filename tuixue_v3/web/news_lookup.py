"""
新闻模块：
- sina lid=2516 (财经要闻) + lid=2517 (7x24快讯) 双源抓取，去重合并
- 缓存 → Redis (cache_store.tx3:news:cache) TTL 30min，跨进程共享
- AI 分析交给 server.py 调用 MiniMax M3，结果回写到 cache.ai
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests as _requests

log = logging.getLogger("tuixue_v3.web.news")

# 兼容旧逻辑:启动时若 Redis 没数据,从 data/news_cache.json 灌入一次
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_LEGACY_CACHE_FILE = DATA_DIR / "news_cache.json"
CACHE_TTL = 30 * 60  # 30 分钟
FETCH_TIMEOUT = 5   # 2026-07-11: 8→5,sina 单 lid 5s 足够
DEFAULT_NUM = 30

# 走 cache_store (Redis 主用 + SQLite fallback)
from .. import cache_store as _cs
from ..cache_store import get_store, K
_store = get_store()

# sina 财经滚动的 lid（实测可用）
LIDS = {
    2516: "财经要闻",
    2517: "7x24快讯",
}


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
                _store.set(K.NEWS, legacy, ttl=CACHE_TTL)
                log.info(f"已从老 news_cache.json 灌入 {len(legacy.get('news', []))} 条")
                return legacy
        except Exception as e:
            log.warning(f"老 news_cache.json 灌入失败: {e}")
    return {"fetched_at": 0, "analyzed_at": 0, "news": [], "ai": {}}


def load_cache() -> dict:
    """公开别名 — server.py 直接读 (避免触发 fetch_news)。"""
    return _load_cache()


def _save_cache(cache: dict) -> None:
    _store.set(K.NEWS, cache, ttl=CACHE_TTL)


def get_cached_news(force_refresh: bool = False, num: int = DEFAULT_NUM) -> dict:
    """
    取新闻缓存（30 分钟内复用，否则刷新抓取，但 AI 不在抓取时强制跑）
    返回 {"fetched_at", "analyzed_at", "news": [...], "ai": {id: {...}}, "_stale": bool}

    2026-07-11: 若 fetch_news 失败且旧 cache 非空,标记 _stale=True 返旧 cache,
    保证 sina 全挂时仍能返非空列表,不写新 fetched_at 避免污染 TTL。
    2026-07-11: 缓存层从 data/news_cache.json 改走 Redis (cache_store.tx3:news:cache)
    """
    with _lock:
        cache = _load_cache()
        age = time.time() - (cache.get("fetched_at") or 0)
        if not force_refresh and age < CACHE_TTL and cache.get("news"):
            log.info(f"news 缓存命中 (age={age:.0f}s)")
            return cache

        log.info(f"news 抓取刷新 (age={age:.0f}s, force={force_refresh})")
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


def save_ai_analysis(ai_map: dict[str, dict]) -> None:
    """把 AI 评分合并进缓存（保留已有）"""
    with _lock:
        cache = _load_cache()
        cache["ai"] = {**(cache.get("ai") or {}), **ai_map}
        cache["analyzed_at"] = int(time.time())
        _save_cache(cache)