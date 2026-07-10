"""
新闻模块：
- sina lid=2516 (财经要闻) + lid=2517 (7x24快讯) 双源抓取，去重合并
- 缓存 data/news_cache.json（30 分钟 TTL，文件级锁防并发刷新）
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

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_FILE = DATA_DIR / "news_cache.json"
CACHE_TTL = 30 * 60  # 30 分钟
FETCH_TIMEOUT = 8
DEFAULT_NUM = 30

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
    url = f"https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid={lid}&num={num}&page=1"
    r = _requests.get(url, timeout=FETCH_TIMEOUT, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.sina.com.cn/",
    })
    if r.status_code != 200:
        log.warning(f"sina lid={lid} HTTP {r.status_code}")
        return []
    try:
        data = r.json().get("result", {}).get("data") or []
    except Exception as e:
        log.warning(f"sina lid={lid} JSON 解析失败: {e}")
        return []
    out = []
    for x in data:
        n = _normalize(x, lid)
        if n:
            out.append(n)
    return out


def fetch_news(limit_per_lid: int = DEFAULT_NUM) -> list[dict]:
    """双 lid 抓取 + 去重 + 按时间倒序"""
    seen: dict[str, dict] = {}
    for lid in LIDS:
        try:
            items = _fetch_sina(lid, limit_per_lid)
            for it in items:
                # 同标题去重（2516/2517 偶有重叠）
                if it["id"] not in seen:
                    seen[it["id"]] = it
        except Exception as e:
            log.warning(f"sina lid={lid} 抓取异常: {e}")
    merged = sorted(seen.values(), key=lambda x: x.get("ctime") or 0, reverse=True)
    return merged[:limit_per_lid * 2]


# ── 缓存读写 ────────────────────────────────────────────────
_lock = threading.Lock()


def _load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {"fetched_at": 0, "analyzed_at": 0, "news": [], "ai": {}}
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception as e:
        log.warning(f"news_cache 读取失败: {e}")
        return {"fetched_at": 0, "analyzed_at": 0, "news": [], "ai": {}}


def _save_cache(cache: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    tmp.replace(CACHE_FILE)


def get_cached_news(force_refresh: bool = False, num: int = DEFAULT_NUM) -> dict:
    """
    取新闻缓存（30 分钟内复用，否则刷新抓取，但 AI 不在抓取时强制跑）
    返回 {"fetched_at", "analyzed_at", "news": [...], "ai": {id: {...}}}
    """
    with _lock:
        cache = _load_cache()
        age = time.time() - (cache.get("fetched_at") or 0)
        if not force_refresh and age < CACHE_TTL and cache.get("news"):
            log.info(f"news 缓存命中 (age={age:.0f}s)")
            return cache

        log.info(f"news 抓取刷新 (age={age:.0f}s, force={force_refresh})")
        news = fetch_news(num)
        cache["news"] = news
        cache["fetched_at"] = int(time.time())
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