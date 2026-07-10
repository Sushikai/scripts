"""
tuixue_v3/web/ai_scoring.py
AI 打分 — screen 候选股 per-stock 评分 + 候选池综合榜

设计要点:
- 复用 server._call_minimax / _parse_ai_json / AI_SYSTEM_PROMPT (lazy import, 避免循环)
- 6 路 fetch (quote/flow/seats/kline/limit_up/sector) 与 server.py:1610-1650 同型
- 4 并发信号量 + asyncio.shield 保护 + 25s 单只/40s 总闸
- 单股失败降级返回 None,不影响整体;综合榜失败整体 None
- SQLite 日内缓存 (date, code, model), TTL = 当日 23:59 (cache_db.get_cached_ai/upsert_ai)

入口:
- score_one(code, *, sector="") -> dict | None
- score_batch(candidates, *, on_progress=None) -> dict
- score_aggregate(scored_candidates) -> dict | None
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Callable

from .. import cache_db
from .. import lib_common as lc

log = logging.getLogger("tuixue_v3.web.ai_scoring")


# ── 并发限流 ──────────────────────────────────────
_AI_SEM = asyncio.Semaphore(4)


@asynccontextmanager
async def _ai_slot():
    async with _AI_SEM:
        yield


# 独立线程池给 per-stock AI 调用(避免占满 server 主池)
_SCORING_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="ai_score")


# ── 6 路 fetch (与 server.py:1609-1630 同型, 模块独立复制) ──────
async def _fetch_stock_context(code: str) -> dict:
    """
    并发拉 6 路数据, 单路异常降级 None, 总闸 14s.
    复用 to_thread 把同步函数卸到后台,不阻塞 event loop.
    """
    from . import fund_flow, seat_lookup
    from .limit_up_context import get_limit_up_context
    from .sector_classify import get_sector as _get_sector
    from .. import data_layer

    def _quote(c: str):
        return lc.fetch_realtime(c)

    def _flow(c: str):
        return fund_flow.get_combined(c, 60)

    def _seats(c: str):
        return seat_lookup.get_stock_seats(c, 10)

    def _kline(c: str):
        try:
            df = data_layer.fetch_daily(c, 60)
            if df is None or df.empty:
                return []
            out = []
            for _, r in df.tail(60).iterrows():
                out.append({
                    "date":     str(r.get("日期", "")),
                    "open":     float(r.get("开盘", 0) or 0),
                    "high":     float(r.get("最高", 0) or 0),
                    "low":      float(r.get("最低", 0) or 0),
                    "close":    float(r.get("收盘", 0) or 0),
                    "volume":   float(r.get("成交量", 0) or 0),
                    "amount":   float(r.get("成交额", 0) or 0),
                    "turnover": float(r.get("换手率", 0) or 0),
                })
            return out
        except Exception:
            return []

    def _limit_up(c: str):
        return get_limit_up_context(c, sector_name=None)

    def _sector(c: str):
        return _get_sector(c)

    async def _wt(coro, sec):
        try:
            return await asyncio.wait_for(coro, timeout=sec)
        except Exception:
            return None

    try:
        quote, flow, seats, kline, limit_up, sector = await asyncio.wait_for(
            asyncio.gather(
                _wt(asyncio.get_event_loop().run_in_executor(_SCORING_EXECUTOR, _quote, code), 4),
                _wt(asyncio.get_event_loop().run_in_executor(_SCORING_EXECUTOR, _flow,  code), 6),
                _wt(asyncio.get_event_loop().run_in_executor(_SCORING_EXECUTOR, _seats, code), 4),
                _wt(asyncio.get_event_loop().run_in_executor(_SCORING_EXECUTOR, _kline, code), 6),
                _wt(asyncio.get_event_loop().run_in_executor(_SCORING_EXECUTOR, _limit_up, code), 6),
                _wt(asyncio.get_event_loop().run_in_executor(_SCORING_EXECUTOR, _sector, code), 5),
            ),
            timeout=14,
        )
    except asyncio.TimeoutError:
        log.warning(f"_fetch_stock_context 总超时 (code={code})")
        return {}

    def _ok(v, default):
        return default if isinstance(v, BaseException) or v is None else v

    return {
        "quote":     _ok(quote, {}),
        "fund_flow": _ok(flow,  {"code": code, "today": None, "history": []}),
        "seats":     _ok(seats, {"code": code, "rows": [], "blacklisted": False,
                                  "seat_count": 0, "total_lhb_rows": 0, "known_groups": [],
                                  "buy_total_wan": None, "sell_total_wan": None}),
        "kline":     _ok(kline, []),
        "limit_up":  _ok(limit_up, {"code": code, "today": None, "recent_5d": [],
                                    "sector_today": [], "summary": "数据拉取失败"}),
        "sector":    _ok(sector, {"code": code, "sw": None, "ai_tags": {"labels": [], "is_main_field": False}}),
    }


# ── 单只 AI ──────────────────────────────────────
async def score_one(code: str, *, sector: str = "", force_refresh: bool = False,
                    global_payload: dict | None = None) -> dict | None:
    """
    命中 SQLite 缓存 → 直接返回 (from_cache=True);
    否则 6 路 fetch + MiniMax 调用,35s 硬闸,失败返 None.
    global_payload: 来自 fetch_global_sentiment() 的 dict,会被压成 ~1.5K 文本塞进 prompt 头部
    """
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not api_key:
        return None

    date = _today_str()
    model = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")

    if not force_refresh:
        cached = cache_db.get_cached_ai(date, code, model)
        if cached:
            cached["sector"] = sector or cached.get("sector", "")
            return cached

    ctx = await _fetch_stock_context(code)
    if not ctx or (not ctx.get("quote") and not ctx.get("kline")):
        log.warning(f"score_one 上下文全空 (code={code})")
        return None

    # 注入全局(美/韩)情绪文本 → _call_minimax 会读取并 prepend
    if global_payload:
        try:
            from . import global_markets as _gm
            ctx["_global_text"] = _gm.render_for_prompt(global_payload, max_chars=1200)
        except Exception as e:
            log.warning(f"render global text 失败: {e}")

    try:
        from . import server as srv  # lazy import 避开循环
        sys_prompt = srv.AI_SYSTEM_PROMPT
        call_fn = srv._call_minimax

        async with _ai_slot():
            try:
                parsed = await asyncio.shield(
                    asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(
                            _SCORING_EXECUTOR,
                            lambda: call_fn(api_key, code, ctx),
                        ),
                        timeout=35,
                    )
                )
            except asyncio.TimeoutError:
                log.warning(f"score_one AI 超时 (code={code})")
                return None

        if not parsed:
            return None

        parsed["from_cache"] = False
        cache_db.upsert_ai(date, code, model, parsed, sector)
        return parsed
    except Exception as e:
        log.warning(f"score_one 失败 (code={code}): {e}")
        return None


# ── 批量 ──────────────────────────────────────
async def score_batch(
    candidates: list[dict],
    *,
    on_progress: Callable | None = None,
    force_refresh: bool = False,
    global_payload: dict | None = None,
) -> dict:
    """
    candidates: [{code, name, sector, ...}] (screen 末段 l4_passed)
    on_progress: (code, ai_dict_or_None) 异步回调, 用于 SSE 推送单只完成事件
    global_payload: 全局情绪,所有股票共用一段;None = 不注入

    返回: {
      "scored": [{code, name, sector, ai}, ...],  # ai 为 None 表示失败
      "fail_count": int,
      "elapsed_sec": float,
    }
    单只失败不拖累其它;返回结构长度与输入一致。
    """
    if not candidates:
        return {"scored": [], "fail_count": 0, "elapsed_sec": 0.0}

    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not api_key:
        log.info("score_batch: MINIMAX_API_KEY 未配置, all ai=null")
        scored = [{"code": c.get("code"), "name": c.get("name"),
                   "sector": c.get("sector") or c.get("recent_hot_sector_name") or "",
                   "ai": None} for c in candidates]
        return {"scored": scored, "fail_count": len(candidates), "elapsed_sec": 0.0}

    # 没传 global_payload 就拉一次(60s 内存缓存在 server 层);失败返 {} 不阻塞
    if global_payload is None:
        try:
            from . import global_markets as _gm
            global_payload = await asyncio.get_event_loop().run_in_executor(
                None, _gm.fetch_global_sentiment,
            ) or {}
        except Exception as e:
            log.warning(f"score_batch fetch_global_sentiment 失败: {e}")
            global_payload = {}

    t0 = time.monotonic()

    async def _run_one(c: dict) -> dict:
        code = c.get("code")
        sector = c.get("sector") or c.get("recent_hot_sector_name") or ""
        ai = await score_one(code, sector=sector, force_refresh=force_refresh,
                             global_payload=global_payload)
        if on_progress:
            try:
                cb = on_progress(code, ai)
                if asyncio.iscoroutine(cb):
                    await cb
            except Exception:
                pass
        return {"code": code, "name": c.get("name") or "",
                "sector": sector, "ai": ai}

    # 全并发受 _AI_SEM (4) 限流;每个任务最多 35s;总闸 40s 兜底
    try:
        scored = await asyncio.wait_for(
            asyncio.gather(*[_run_one(c) for c in candidates], return_exceptions=False),
            timeout=40,
        )
    except asyncio.TimeoutError:
        log.warning(f"score_batch 总闸超时(40s), candidates={len(candidates)}")
        # 尽力收集已完成的;未完成的置 ai=None
        scored = [{"code": c.get("code"), "name": c.get("name") or "",
                   "sector": c.get("sector") or "", "ai": None} for c in candidates]

    fail = sum(1 for s in scored if s.get("ai") is None)
    return {
        "scored":   scored,
        "fail_count": fail,
        "elapsed_sec": round(time.monotonic() - t0, 2),
    }


# ── 综合榜 ──────────────────────────────────────
async def score_aggregate(scored: list[dict], *,
                          global_payload: dict | None = None) -> dict | None:
    """
    scored: [{code, name, sector, ai_or_None}, ...]
    输入是 score_batch 输出(摘要),不是 raw 行情,避免与子结论内部矛盾
    prompt 限制 ~1200 tokens user 段
    global_payload: 同 score_batch 的全局情绪,作为 system prompt 前置

    返回: {
      "ranking": [{code, name, recommendation: "强烈买入|买入|观望|回避",
                   reason: str (≤40字)}, ...],
      "overall_view": str,
      "ts": epoch,
    }
    失败返 None
    """
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not api_key:
        return None
    valid = [s for s in scored if s.get("ai")]
    if not valid:
        log.warning("score_aggregate: 无有效 ai 结果")
        return None

    date = _today_str()
    model = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")

    # 缓存命中则省一次
    cached_agg = cache_db.get_cached_aggregate(date, model)
    if cached_agg and valid[-1].get("ai", {}).get("ts_updated", 0) <= cached_agg.get("ts", 0):
        # 仅当所有 scored 都来自缓存才复用
        if all(s.get("ai", {}).get("from_cache") for s in valid):
            cached_agg["from_cache"] = True
            return cached_agg

    # 可选:把 global context 拼到 user 段顶部 → AI 看到全局风险偏好
    global_text = ""
    if global_payload:
        try:
            from . import global_markets as _gm
            global_text = _gm.render_for_prompt(global_payload, max_chars=900)
        except Exception:
            pass

    # 拼 user 段:各股的精简摘要
    lines = ["请基于下方已给出的各股 AI 判定,综合排序并点评。"]
    lines.append("【输入 · 各股 AI 评分】")
    for s in valid:
        ai = s.get("ai") or {}
        role = ai.get("role") or "中军"
        lines.append(f"- {s.get('code')} {s.get('name')} ({s.get('sector') or '-'}) "
                     f"角色={role} "
                     f"判定={ai.get('verdict','-')} 确信度={ai.get('conviction',0)} "
                     f"理由={ai.get('summary','')[:60]}")
    user_content = "\n".join(lines)[:1500]
    if global_text:
        user_content = f"{global_text}\n\n--- 个股子结论 ---\n{user_content}"

    system_content = (
        "你是退学炒股 AI 综合评论员。基于各股的子结论,给出整个候选池的综合排名 + 板块整体点评。\n\n"
        "【排序优先级】1) 龙头 > 中军 > 杂毛  2) 判定 强烈买入 > 买入 > 观望 > 回避  3) conviction 高分优先\n\n"
        "【输出严格 JSON,不要 markdown 围栏】\n"
        '{"ranking": [\n'
        '  {"code":"600519","name":"XX","role":"龙头|中军|杂毛",\n'
        '   "recommendation":"强烈买入|买入|观望|回避",\n'
        '   "reason":"一句话理由(≤40字,需含角色定位 + 核心逻辑)"},\n'
        '  ...\n'
        '],\n'
        '"overall_view":"整体点评(80字内,覆盖板块联动/龙头中军杂毛结构/明日观察点)"}\n'
    )

    # 直接走 MiniMax,不复用 _call_minimax(避免循环);失败返 None
    url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1/text/chatcompletion_v2")
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 1500,
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }

    try:
        import requests as _req
        def _do_call():
            r = _req.post(url, json=body, headers=headers, timeout=20)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:120]}")
            j = r.json()
            text = (j.get("choices") or [{}])[0].get("message", {}).get("content", "")
            return _parse(text) if text else {}

        parsed = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(_SCORING_EXECUTOR, _do_call),
            timeout=25,
        )
    except Exception as e:
        log.warning(f"score_aggregate AI 失败: {e}")
        return None

    if not parsed or not isinstance(parsed, dict):
        return None

    if "ranking" not in parsed and "overall_view" not in parsed:
        return None

    parsed["ts"] = time.time()
    parsed["from_cache"] = False

    try:
        cache_db.upsert_aggregate(date, model, parsed)
    except Exception:
        pass
    return parsed


def _parse(text: str) -> dict:
    """宽松 JSON 解析(与 server._parse_ai_json 同型)"""
    import re
    t = text.strip()
    if t.startswith("```"):
        m = re.search(r"```(?:json)?\s*([\s\S]+?)(?:```|$)", t)
        if m:
            t = m.group(1).strip()
    if not t.startswith("{"):
        m = re.search(r"\{[\s\S]+\}", t)
        if m:
            t = m.group(0)
    try:
        return json.loads(t)
    except Exception:
        idx = t.rfind("}")
        if idx > 0:
            try:
                return json.loads(t[:idx + 1])
            except Exception:
                pass
    return {}


def _today_str() -> str:
    return datetime.date.today().strftime("%Y%m%d")
