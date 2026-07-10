"""
AI 对话框 (2026-07-10)

- 用户问某只股票怎么样 → 拉盘面 + 铁律 → 返打板建议
- 多轮对话:把历史 N 轮 user/assistant 拼到 messages 列表(滑动窗口)
- 内置缓存 (内存,per-session,30min TTL) - 同问题不重复打 AI

入参: code (可选), message, history=[{role, content}]
返参: {reply, suggestions: [...], rules_hit: [...], used_ctx_keys: [...]}

实现要点:
- 复用 _call_minimax 但 user prompt 不再带 K线 — 由我们组装更可控的 user
- 拉 code 的盘面:quote/flow/seats/kline/limit_up/sector/news 全集
- system = laws.as_prompt() + "你是用户的看盘助手 ..."
- max_tokens=900 (对话不需要长文)
"""
from __future__ import annotations

import json
import logging
import os
import time as systime
from collections import deque
from typing import Any

import requests as _rq

log = logging.getLogger("tuixue_v3.web.ai_chat")

_CACHE: dict[str, tuple[float, str]] = {}
_TTL = 1800  # 30min
_MAX_HIST = 6  # 6 轮 = 12 条 messages


def _cache_get(key: str) -> str | None:
    e = _CACHE.get(key)
    if not e or (systime.time() - e[0]) > _TTL:
        _CACHE.pop(key, None)
        return None
    return e[1]


def _cache_set(key: str, val: str) -> None:
    _CACHE[key] = (systime.time(), val)


def _build_ctx(code: str | None) -> dict:
    """拉盘面给 AI 当参考。"""
    if not code or len(code) != 6 or not code.isdigit():
        return {}
    from .. import lib_common as lc
    from . import fund_flow, seat_lookup, sector_classify
    from . import holder_lookup
    ctx: dict[str, Any] = {}
    try:
        q = lc.fetch_realtime(code)
        if q:
            ctx["quote"] = q
    except Exception as e:
        log.debug(f"chat ctx quote {code}: {e}")
    try:
        ctx["fund_flow_today"] = fund_flow.get_main_flow(code)
    except Exception:
        pass
    try:
        ctx["seats"] = seat_lookup.get_stock_seats(code, lookback_days=10)
    except Exception:
        pass
    try:
        df = lc.fetch_daily(code, days=30)
        if df is not None and not df.empty:
            # 用户要求 (2026-07-10): 必须含前 10 日 K线
            ctx["kline_recent_10d"] = df.tail(10).to_dict(orient="records")
    except Exception:
        pass
    try:
        ctx["sector"] = sector_classify.get_sector(code, force_refresh=False)
    except Exception:
        pass
    try:
        ctx["holders"] = holder_lookup.fetch_holder_info(code)
    except Exception:
        pass
    return ctx


def _build_system(code: str | None, ctx: dict) -> str:
    from .. import laws
    base = laws.as_prompt()
    intro = """你是用户的看盘/打板助手。
- 基于上面的"42条铁律"做约束
- 用户问什么答什么,不主动给未问的内容
- 涉及代码 [code] 时,严格用用户给的代码,不要自己编
- 回答不超过 300 字(用户要详细才展开)
- 涉及预测/价格/买入时,必须明确写 "非投资建议"
- 末尾用 1-2 行给"具体下一步" (例:看次日开盘量比 / 设止损位)
"""
    ctx_str = ""
    if ctx:
        # 截短避免超 1k tokens
        ctx_str = "\n\n用户提到代码的当前盘面:\n" + json.dumps(ctx, ensure_ascii=False, default=str)[:1500]
    return base + "\n\n" + intro + ctx_str


def _extract_code(message: str) -> str | None:
    """从用户消息中识别 6 位股票代码。"""
    import re
    m = re.search(r"\b(\d{6})\b", message)
    if m:
        c = m.group(1)
        # 排除看起来不像股票代码的(纯数字常见词,例如日期)
        if c.startswith(("0", "3", "6", "9", "5")):
            return c
    return None


def chat(message: str, code: str | None = None, history: list[dict] | None = None) -> dict:
    """主入口: 用户发一句话, 返 AI 回复。"""
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        return {"reply": "⚠ MINIMAX_API_KEY 未配置,AI 对话暂不可用。", "suggestions": [], "rules_hit": [], "code": code}

    # 1) 找 code
    if not code:
        code = _extract_code(message)
    ctx = _build_ctx(code) if code else {}
    sys_p = _build_system(code, ctx)

    # 2) 组装 messages (滑动窗口)
    messages: list[dict] = [{"role": "system", "content": sys_p}]
    if history:
        for h in history[-(_MAX_HIST * 2):]:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                messages.append({"role": h["role"], "content": h["content"][:600]})
    messages.append({"role": "user", "content": message[:1000]})

    # 3) 缓存 key
    cache_key = f"{code or '_'}:{message[:200]}:{len(history or [])}"
    cached = _cache_get(cache_key)
    if cached:
        return json.loads(cached)

    # 4) 调 AI
    url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1/text/chatcompletion_v2")
    model = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": 900,
        "temperature": 0.4,
    }
    try:
        r = _rq.post(url, json=body,
                     headers={"Authorization": f"Bearer {api_key}",
                              "Content-Type": "application/json"},
                     timeout=20)
    except _rq.exceptions.Timeout:
        return {"reply": "⏱ AI 响应超时(>20s),请稍后重试", "suggestions": [], "rules_hit": [], "code": code}
    except Exception as e:
        log.warning(f"AI chat 网络失败: {e}")
        return {"reply": f"⚠ 网络异常: {type(e).__name__}", "suggestions": [], "rules_hit": [], "code": code}
    if r.status_code != 200:
        return {"reply": f"⚠ AI 服务异常 HTTP {r.status_code}: {r.text[:150]}",
                "suggestions": [], "rules_hit": [], "code": code}
    try:
        j = r.json()
        reply = (j.get("choices", [{}])[0].get("message", {}) or {}).get("content", "").strip()
    except Exception as e:
        return {"reply": f"⚠ AI 响应解析失败: {e}", "suggestions": [], "rules_hit": [], "code": code}
    if not reply:
        # finish=length 截断,重试一次大窗口
        body["max_tokens"] = 1800
        try:
            r2 = _rq.post(url, json=body, headers={"Authorization": f"Bearer {api_key}",
                                                   "Content-Type": "application/json"}, timeout=20)
            j2 = r2.json()
            reply = (j2.get("choices", [{}])[0].get("message", {}) or {}).get("content", "").strip()
        except Exception:
            pass
    if not reply:
        reply = "(AI 返回为空)"
    # 5) 解析建议 + 铁律命中
    suggestions = _extract_suggestions(reply)
    rules_hit = _extract_rules(reply)
    used_ctx_keys = list(ctx.keys()) if ctx else []
    out = {
        "reply": reply,
        "suggestions": suggestions,
        "rules_hit": rules_hit,
        "code": code,
        "used_ctx_keys": used_ctx_keys,
    }
    _cache_set(cache_key, json.dumps(out, ensure_ascii=False))
    return out


def _extract_suggestions(reply: str) -> list[str]:
    """从回复中提取"下一步"建议行。"""
    lines = [l.strip() for l in reply.split("\n") if l.strip()]
    out = []
    for l in lines:
        if any(k in l for k in ("建议", "→", "👉", "⚠", "止损", "止盈", "下一步", "关注", "看", "等")):
            out.append(l[:80])
    return out[:5]


def _extract_rules(reply: str) -> list[str]:
    """从回复中提取铁律编号 (#1, #42 等)。"""
    import re
    return list(set(re.findall(r"#\d{1,3}", reply)))[:10]