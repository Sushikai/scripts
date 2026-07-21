"""
web/ai_client.py — 统一 AI 调用客户端(R1+R3+R9 基础)

设计目标:
  1. 单一入口负责 HTTP + 重试 + 退避 + 熔断 + 解析 + 指标
  2. 把散在 server.py / ai_scoring.py / ai_chat.py / review.py / news_lookup 4+ 处重复的
     "_requests.post + 2 attempt + ReadTimeout + json 解码"逻辑收拢
  3. 对所有用户喂给 LLM 的 prompt 自动加 boundary 标记(R2 prompt 注入防御)
  4. 对所有 AI 输出自动 schema 校验 + 拒答兜底(R7)
  5. 对所有调用自动记录 metrics(R9)
  6. 对 out-of-range 字段自动修复 / clamp
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable

import requests as _requests

log = logging.getLogger("tuixue_v3.web.ai_client")

# ───────────────────────────────────────────────────────────
# Metrics — 全进程共享, observe("/api/ai/metrics") 暴露
# ───────────────────────────────────────────────────────────
@dataclass
class _Bucket:
    calls: int = 0
    ok: int = 0
    fail: int = 0
    retries: int = 0
    parse_fail: int = 0
    schema_fail: int = 0
    cached: int = 0
    degraded: int = 0           # 走到了降级返回(default verdict=-)
    total_latency_ms: float = 0.0
    last_latency_ms: float = 0.0
    ttfb_ms: float = 0.0
    total_tokens: int = 0      # R7: 累计消耗 token
    total_cost_usd: float = 0.0  # R7: 累计成本估算
    model: str = "unknown"
    name: str = "unknown"
    last_5_min: deque = field(default_factory=lambda: deque(maxlen=256))


# R7: 模型 token 单价 (USD per 1k tokens) — 主流 MiniMax/Claude/GPT 估算
# 实际计费按 provider 公告,这里用保守价,前端展示给用户参考
_MODEL_PRICES = {
    "MiniMax-M3":  {"input": 0.0008, "output": 0.0024},   # 占位价
    "claude-opus-4-7":   {"input": 0.015, "output": 0.075},
    "claude-sonnet-4-6": {"input": 0.003, "output": 0.015},
    "claude-haiku-4-5":  {"input": 0.0008, "output": 0.004},
    "gpt-4o":     {"input": 0.005, "output": 0.015},
    "gpt-4o-mini":{"input": 0.00015, "output": 0.0006},
}


def _estimate_cost_usd(model: str, total_tokens: int) -> float:
    """粗估一次调用的成本 (美元)。只用于指标展示,不作为计费依据。"""
    if total_tokens <= 0:
        return 0.0
    p = _MODEL_PRICES.get(model) or _MODEL_PRICES.get("MiniMax-M3")
    if not p:
        return 0.0
    # 简化: 50/50 input/output (实际 server 不会给 input/output 拆分)
    blended = (p["input"] + p["output"]) / 2
    return round(blended * total_tokens / 1000, 6)


_BUCKETS: dict[str, _Bucket] = {}
_BUCKETS_LOCK = threading.Lock()

# 进程级熔断:同一个 name 在 30s 内连续 4 次失败 → 熔断 10s,快速 fail
_CB = {}                       # name -> {"fail_streak": int, "open_until": float, "fail_ts": deque}
_CB_LOCK = threading.Lock()
_CB_FAIL_WINDOW = 30.0
_CB_FAIL_THRESHOLD = 4
_CB_OPEN_DURATION = 10.0

# R6: 全局 AI inflight 限流 — 防止 score_batch + stock_ai_analysis 并发把上游冲挂
import threading as _threading
_AI_INFLIGHT = 0
_AI_INFLIGHT_MAX = int(os.environ.get("AI_INFLIGHT_MAX", "12"))
_AI_INFLIGHT_LOCK = _threading.Lock()
_AI_INFLIGHT_COND = _threading.Condition(_AI_INFLIGHT_LOCK)


def _inflight_acquire() -> bool:
    """阻塞式 acquire 全局 inflight slot(>30s 视为超时,强制 fail)."""
    global _AI_INFLIGHT
    import time as _t
    with _AI_INFLIGHT_COND:
        deadline = _t.monotonic() + 30.0
        while _AI_INFLIGHT >= _AI_INFLIGHT_MAX:
            wait = max(0.05, deadline - _t.monotonic())
            if wait <= 0:
                log.warning(f"AI inflight 全局等待超时 (max={_AI_INFLIGHT_MAX})")
                return False
            _AI_INFLIGHT_COND.wait(timeout=wait)
        _AI_INFLIGHT += 1
        return True


def _inflight_release() -> None:
    with _AI_INFLIGHT_COND:
        global _AI_INFLIGHT
        _AI_INFLIGHT = max(0, _AI_INFLIGHT - 1)
        _AI_INFLIGHT_COND.notify()


@contextmanager
def _inflight_ctx():
    if not _inflight_acquire():
        raise AICallError("global AI inflight slot timeout (>30s)")
    try:
        yield
    finally:
        _inflight_release()


def _cb_should_open(name: str) -> bool:
    """Open the circuit if too many recent failures."""
    now = time.monotonic()
    with _CB_LOCK:
        cb = _CB.get(name)
        if not cb:
            return False
        if cb["open_until"] > now:
            return True
        # 清理超时 fail streak
        cb["fail_streak_ts"] = deque(t for t in cb.get("fail_streak_ts", []) if now - t < _CB_FAIL_WINDOW)
        if cb["open_until"] <= now and len(cb["fail_streak_ts"]) >= _CB_FAIL_THRESHOLD:
            cb["open_until"] = now + _CB_OPEN_DURATION
            log.warning(f"AI circuit breaker OPEN name={name} ({_CB_OPEN_DURATION}s)")
            return True
        return False


def _cb_record_fail(name: str) -> None:
    now = time.monotonic()
    with _CB_LOCK:
        cb = _CB.setdefault(name, {"fail_streak": 0, "open_until": 0.0, "fail_streak_ts": deque()})
        cb["fail_streak"] += 1
        cb.setdefault("fail_streak_ts", deque()).append(now)


def _cb_record_ok(name: str) -> None:
    with _CB_LOCK:
        cb = _CB.get(name)
        if cb:
            cb["fail_streak"] = 0
            cb["open_until"] = 0.0


def _bucket(name: str, model: str) -> _Bucket:
    with _BUCKETS_LOCK:
        b = _BUCKETS.get(name)
        if not b:
            b = _Bucket(name=name, model=model)
            _BUCKETS[name] = b
        else:
            b.model = model
        return b


def get_metrics() -> dict:
    """渲染全进程 AI 调用指标 — 暴露给 /api/ai/metrics."""
    with _BUCKETS_LOCK:
        out = {"buckets": {}, "ts": time.time()}
        for name, b in _BUCKETS.items():
            avg = (b.total_latency_ms / b.calls) if b.calls else 0.0
            ok_pct = round(100 * b.ok / b.calls, 2) if b.calls else 0.0
            out["buckets"][name] = {
                "calls":          b.calls,
                "ok":             b.ok,
                "fail":           b.fail,
                "retries":        b.retries,
                "parse_fail":     b.parse_fail,
                "schema_fail":    b.schema_fail,
                "cached":         b.cached,
                "degraded":       b.degraded,
                "ok_pct":         ok_pct,
                "avg_latency_ms": round(avg, 1),
                "last_latency_ms":round(b.last_latency_ms, 1),
                "last_5_min_evt_count": len(b.last_5_min),
                "model":          b.model,
                "total_tokens":   b.total_tokens,
                "total_cost_usd": round(b.total_cost_usd, 4),
            }
        # R7: 累加所有 bucket 的总成本,便于前端 dashboard 一眼看出
        total_cost = sum(b.total_cost_usd for b in _BUCKETS.values())
        total_tokens = sum(b.total_tokens for b in _BUCKETS.values())
        out["total_cost_usd"] = round(total_cost, 4)
        out["total_tokens"]   = total_tokens
    # R-perf-020: 暴露熔断状态给前端 degraded UI(open=断路中,剩余冷却秒数)
    now = time.monotonic()
    breakers = {}
    any_open = False
    with _CB_LOCK:
        for name, cb in _CB.items():
            open_until = cb.get("open_until", 0.0)
            is_open = open_until > now
            if is_open:
                any_open = True
            breakers[name] = {
                "open": is_open,
                "cooldown_sec": round(max(0.0, open_until - now), 1),
                "fail_streak": cb.get("fail_streak", 0),
            }
    out["breakers"] = breakers
    out["any_breaker_open"] = any_open
    return out


def _record_metric(b: _Bucket, *, ok: bool, latency_ms: float, retry: int = 0,
                   parse_fail: bool = False, schema_fail: bool = False,
                   cached: bool = False, degraded: bool = False) -> None:
    b.calls += 1
    b.total_latency_ms += latency_ms
    b.last_latency_ms = latency_ms
    b.last_5_min.append({"ok": ok, "latency_ms": latency_ms, "ts": time.time()})
    if cached:
        b.cached += 1
    if retry:
        b.retries += retry
    if degraded:
        b.degraded += 1
    if ok:
        b.ok += 1
    else:
        b.fail += 1
    if parse_fail:
        b.parse_fail += 1
    if schema_fail:
        b.schema_fail += 1


# ───────────────────────────────────────────────────────────
# 通用 JSON sanitize — 把 NaN/Inf/控制字符清理成 JSON 安全
# ───────────────────────────────────────────────────────────
_CTRL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def sanitize_for_json(obj: Any) -> Any:
    """递归清洗:NaN/Inf→None,Timestamp→iso,bytes→str,deep control char→strip.
    用户喂给 LLM 的 ctx/news/history 必须先过这一道。"""
    if obj is None:
        return None
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {str(k): sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_for_json(x) for x in obj]
    if isinstance(obj, (bytes, bytearray)):
        try:
            return obj.decode("utf-8", errors="replace")
        except Exception:
            return str(obj)
    if isinstance(obj, str):
        return _CTRL_CHAR_RE.sub("", obj)
    # pandas Timestamp / numpy scalar
    try:
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        if hasattr(obj, "item"):
            return obj.item()
    except Exception:
        return str(obj)
    return obj


def json_dumps_safe(obj: Any, **kw) -> str:
    """ensure_ascii=False + 处理 NaN/Inf + 中文 — 给 LLM 拼 prompt 用。"""
    kw.setdefault("ensure_ascii", False)
    kw.setdefault("default", str)
    return json.dumps(sanitize_for_json(obj), **kw)


# ───────────────────────────────────────────────────────────
# 截断 — 按 tokens 粗估(中文 ~1.5字/token,英文 ~0.5字/token)
# ───────────────────────────────────────────────────────────
def estimate_tokens(s: str) -> int:
    if not s:
        return 0
    # 中文 + 全角算 1 token/字, 英文单词 0.5 token/字母
    chinese = sum(1 for c in s if ord(c) > 127)
    return max(1, int(chinese * 1.2 + (len(s) - chinese) * 0.25))


def truncate_to_tokens(s: str, max_tokens: int, *, suffix: str = "...(已截断)") -> str:
    """按粗略 token 数截断 — 防止 user_content 触发 400 / 性能塌方。"""
    if estimate_tokens(s) <= max_tokens:
        return s
    # 二分查找
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(s[:mid]) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return s[:lo] + suffix


def cap_text(s: str | None, max_chars: int = 600, *, ellipsis: str = "…") -> str:
    """粗略字符数截断 — 给单行标签/标题用。"""
    if not s:
        return ""
    s = str(s).strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars].rstrip() + ellipsis


# ───────────────────────────────────────────────────────────
# Prompt 注入防御 — boundary 标签 + 用户控制字符剥离
# ───────────────────────────────────────────────────────────
_INJECTION_TOKENS = (
    "ignore previous", "ignore above", "ignore the above", "disregard previous",
    "你是", "你现在是", "你必须", "system prompt", "system:", "assistant:",
    "forget your rules", "reveal your prompt", "ignore your",
    "</system>", "</ctx>", "</user>", "</assistant>",
)


def is_injection_attempt(s: str) -> bool:
    """用户/外部消息里出现强注入标志位 → True(由调用方决定是否拦截)。"""
    if not s:
        return False
    lower = s.lower()
    for kw in _INJECTION_TOKENS:
        if kw in lower:
            return True
    # 含未配对 <system> / <ctx> 标签
    if s.count("<system>") != s.count("</system>"):
        return True
    return False


def wrap_prompt(tag: str, content: str, *, max_chars: int | None = None) -> str:
    """用 boundary 标签包住不可信内容,告知 LLM 边界。"""
    s = (content or "").strip()
    if max_chars:
        s = cap_text(s, max_chars)
    return f"<{tag}>\n{s}\n</{tag}>"


# ───────────────────────────────────────────────────────────
# 强鲁棒 JSON 解析 — R3
#   处理:
#   - markdown 围栏
#   - 头尾杂字符
#   - 单引号
#   - 末尾逗号
#   - 截断(未闭合引号/花括号)
#   - 转义残留
# ───────────────────────────────────────────────────────────
_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]+?)(?:```|$)", re.IGNORECASE)
_BODY_RE = re.compile(r"\{[\s\S]+")
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_PYTHON_LITERAL_RE = re.compile(r":\s*(True|False|None)\b")
# R-sec-007: 清洗错误体里的 API key / cookie / token
_AUTH_RE = re.compile(r'(?i)\b(authorization|x-api-key|api[_-]?key|cookie|set-cookie|token|secret)\b\s*[=:]\s*["\']?([^"\',;}{]+)')
_BEARER_RE = re.compile(r"(?i)bearer\s+([a-z0-9._\-]{8,})")
_SK_PREFIX_RE = re.compile(r"sk-[a-z0-9]{8,}", re.I)


def _scrub_secrets(text: str | None) -> str:
    """从错误体/响应里清掉 API key / token / cookie 等敏感片段。

    上游常把请求头或回带完整片段放 4xx/5xx 错误里,这些字段进 last_err 后会:
      - 进 log (磁盘)
      - 进 toast/前端 (网络)
      - 进抛出异常的 str
    全部都要先 scrub。返回原长度基本一致,但所有 key 字段替换成 '[REDACTED]'。
    """
    if not text:
        return text or ""
    s = text
    s = _AUTH_RE.sub(r"\1=[REDACTED]", s)
    s = _BEARER_RE.sub("Bearer [REDACTED]", s)
    s = _SK_PREFIX_RE.sub("sk-[REDACTED]", s)
    return s


def _try_json(s: str) -> dict | None:
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except Exception:
        return None


def _strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        m = _FENCE_RE.search(s)
        if m:
            return m.group(1).strip()
    return s


def _python_to_json(s: str) -> str:
    return _PYTHON_LITERAL_RE.sub(lambda m: ": true" if m.group(1) == "True" else (
        ": false" if m.group(1) == "False" else "null"), s)


def _fix_truncated(s: str) -> str:
    """AI 输出常见截断:"unterminated string" / 未闭合括号 — 补到能 json.loads."""
    if not s:
        return s
    # 数 stack 估计未闭合的 [ { "
    out = []
    stack = []
    in_str = False
    esc = False
    quote = '"'
    for c in s:
        out.append(c)
        if esc:
            esc = False
            continue
        if c == '\\':
            if in_str:
                esc = True
            continue
        if in_str:
            if c == quote:
                in_str = False
            continue
        if c in ('"', "'"):
            in_str = True
            quote = c
            continue
        if c == '{':
            stack.append('}')
        elif c == '[':
            stack.append(']')
        elif c in ('}', ']'):
            if stack:
                stack.pop()
    if in_str:
        out.append('"')
    out.extend(reversed(stack))
    return "".join(out)


def parse_json_loose(text: str) -> dict:
    """极宽松 JSON 解析 — 兼容 markdown 围栏 / 单引号 / 截断 / 末尾逗号 / Python 字面量。

    返回 dict;解析失败 → 退到正则逐字段抢救 + 返回 {}。
    """
    if text is None:
        return {}
    s = _strip_fence(text)
    # 单引号 → 双引号(只在 { [ 边界外)
    if "'" in s and '"' not in s:
        s = s.replace("'", '"')
    # Python 字面量
    s = _python_to_json(s)
    # 截断补救
    s_fix = _fix_truncated(s)

    # 1) 标准
    v = _try_json(s)
    if v is not None:
        return v
    # 2) 截断补救
    v = _try_json(s_fix)
    if v is not None:
        return v
    # 3) 去末尾逗号
    s2 = _TRAILING_COMMA_RE.sub(r"\1", s_fix)
    v = _try_json(s2)
    if v is not None:
        return v
    # 4) 去掉最后一段(可能截断在数组里)
    for cand in (s, s_fix, s2):
        # 找到第一个 { 到最后一个 } 完整闭合
        start = cand.find("{")
        if start < 0:
            continue
        # 数括号配对
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(cand)):
            c = cand[i]
            if esc:
                esc = False
                continue
            if c == '\\':
                esc = True
                continue
            if in_str:
                if c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    v = _try_json(cand[start:i+1])
                    if v is not None:
                        return v
                    break
    # 5) 全部失败 → 正则逐字段抢救
    return _rescue_fields(s)


def _rescue_fields(text: str) -> dict:
    """从截断文本里尽可能抢救结构化字段."""
    def _str(field: str) -> str | None:
        m = re.search(rf'"{field}"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        return m.group(1) if m else None

    def _num(field: str) -> float | None:
        m = re.search(rf'"{field}"\s*:\s*(-?\d+(?:\.\d+)?)', text)
        return float(m.group(1)) if m else None

    def _int(field: str) -> int | None:
        v = _num(field)
        return int(v) if v is not None else None

    def _arr(field: str) -> list:
        m = re.search(rf'"{field}"\s*:\s*\[([\s\S]*?)\]', text)
        if not m:
            return []
        items = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
        return items[:8]

    def _layers() -> dict:
        m = re.search(r'"layer_pass"\s*:\s*\{([\s\S]+?)\}', text)
        out = {"L1_风控": None, "L2_周期主线": None, "L3_形态": None, "L4_分时": None}
        if not m:
            return out
        block = m.group(1)
        for name in out:
            nm = re.search(rf'"{name}"\s*:\s*(true|false|null|"true"|"false")',
                           block, re.IGNORECASE)
            if nm:
                v = nm.group(1).lower().strip('"')
                out[name] = None if v == "null" else (v == "true")
        return out

    out = {
        "verdict":       _str("verdict") or "观望",
        "role":          _str("role") or "中军",
        "conviction":    _int("conviction") if _int("conviction") is not None else 30,
        "layer_pass":    _layers(),
        "rules_passed":  _arr("rules_passed"),
        "rules_failed":  _arr("rules_failed"),
        "key_risks":     _arr("key_risks") or ["AI 返回被截断或格式异常,部分数据已尽力恢复"],
        "summary":       (_str("summary") or text[:200])[:200],
    }
    return out


# ───────────────────────────────────────────────────────────
# 统一 schema 校验 (R7)
# ───────────────────────────────────────────────────────────
VERDICT_WHITELIST = {"买", "观望", "回避", "-", "强烈买入", "卖出", "及格", "失误", "严重失误", "优", "正常"}
ROLE_WHITELIST = {"龙头", "中军", "杂毛", "main", "second", "noise", "—", "-"}
CRASH_WHITELIST = {"高", "中高", "中", "低", "无", "未知"}
CONV_MIN, CONV_MAX = 0, 100


def _clamp(v: float | int, lo: float, hi: float) -> float | int:
    if v is None:
        return lo
    try:
        v = float(v)
    except Exception:
        return lo
    return max(lo, min(hi, v))


def _check_whitelist(v: Any, wl: set, fallback: str) -> str:
    if v is None:
        return fallback
    s = str(v).strip()
    return s if s in wl else fallback


def normalize_ai_verdict(d: dict | None) -> dict:
    """对 AI 返回的 verdict 字典做白名单 + clamp + 默认值兜底。"""
    if not isinstance(d, dict):
        return {"verdict": "-", "role": "中军", "conviction": 0,
                "layer_pass": {}, "rules_passed": [], "rules_failed": [],
                "key_risks": [], "summary": "AI 返回非 dict"}

    out = dict(d)
    out["verdict"]    = _check_whitelist(d.get("verdict"), VERDICT_WHITELIST, "观望")
    out["role"]       = _check_whitelist(d.get("role"), ROLE_WHITELIST, "中军")
    out["conviction"] = int(_clamp(d.get("conviction"), CONV_MIN, CONV_MAX))
    out.setdefault("layer_pass", {})
    out["rules_passed"] = list(d.get("rules_passed") or [])[:10]
    out["rules_failed"] = list(d.get("rules_failed") or [])[:10]
    out["key_risks"]    = list(d.get("key_risks") or [])[:10]
    out["summary"]      = cap_text(str(d.get("summary") or ""), 200)
    out.setdefault("ts_updated", time.time())
    return out


def normalize_crash_risk(d: dict | None) -> dict:
    if not isinstance(d, dict):
        return {"crash_risk": "未知", "verdict": "观望", "conviction": 0,
                "signals": [], "signal_count": 0, "rule_violations": [],
                "funding_skew": {}, "summary": "AI 返回非 dict"}
    out = dict(d)
    out["crash_risk"] = _check_whitelist(d.get("crash_risk"), CRASH_WHITELIST, "未知")
    out["verdict"]    = _check_whitelist(d.get("verdict"), {"回避", "观望", "正常", "-"}, "观望")
    out["conviction"] = int(_clamp(d.get("conviction"), CONV_MIN, CONV_MAX))
    out["signals"]    = list(d.get("signals") or [])[:20]
    out["signal_count"] = int(_clamp(d.get("signal_count"), 0, 20))
    out["rule_violations"] = list(d.get("rule_violations") or [])[:10]
    out.setdefault("funding_skew", {})
    out["summary"]     = cap_text(str(d.get("summary") or ""), 200)
    return out


def normalize_chat_reply(reply: str | None) -> str:
    if not reply:
        return "(AI 返回为空)"
    s = str(reply).strip()
    return s[:6000]


# ───────────────────────────────────────────────────────────
# R4 缓存污染防御 — 校验历史缓存条目是否还是合法 schema
# ───────────────────────────────────────────────────────────
REQUIRED_VERDICT_FIELDS = ("verdict", "role", "conviction")


def is_valid_cached_verdict(d: Any) -> bool:
    """校验 cache_db 读出的 verdict 字典是否合法;False 表示缓存污染,调用方应失效并重算。"""
    if not isinstance(d, dict):
        return False
    for k in REQUIRED_VERDICT_FIELDS:
        if k not in d:
            return False
    if not isinstance(d.get("conviction"), (int, float)):
        return False
    if d.get("verdict") not in VERDICT_WHITELIST:
        return False
    if d.get("role") not in ROLE_WHITELIST:
        return False
    return True


REQUIRED_CRASH_FIELDS = ("crash_risk", "verdict", "conviction")


def is_valid_cached_crash(d: Any) -> bool:
    if not isinstance(d, dict):
        return False
    for k in REQUIRED_CRASH_FIELDS:
        if k not in d:
            return False
    if d.get("crash_risk") not in CRASH_WHITELIST:
        return False
    if d.get("verdict") not in {"回避", "观望", "正常", "-"}:
        return False
    if not isinstance(d.get("conviction"), (int, float)):
        return False
    return True


# ───────────────────────────────────────────────────────────
# 网络客户端 — R1 + R9
#   - 2-3 次 attempt (1+2 或 1+2+3)
#   - 429/5xx → 指数退避
#   - 4xx(非 429) → 立刻返
#   - 熔断器
#   - 统一超时
# ───────────────────────────────────────────────────────────
@dataclass
class CallSpec:
    """describe a single AI call."""
    url: str
    headers: dict
    body: dict
    name: str                          # metric tag, e.g. "main_verdict" / "crash_risk"
    model: str
    timeout: float = 35.0
    attempts: tuple = (1, 2)           # (1+1, 1+2): actual sleep base = idx+1
    retry_on_status: tuple = (429, 500, 502, 503, 504)
    max_tokens_alts: tuple = (3500, 4000)
    fallback_max_tokens: int = 4000


class AICallError(Exception):
    def __init__(self, msg: str, *, status: int | None = None, attempts: int = 1,
                 last_body: str | None = None):
        super().__init__(msg)
        self.status = status
        self.attempts = attempts
        self.last_body = (last_body or "")[:300]


def call(spec: CallSpec) -> tuple[str, dict, dict]:
    """统一 AI 调用 — 返回 (raw_text, parsed_dict, info)。

    info: {attempts, retries, tokens, latency_ms, ttfb_ms, status}
    失败抛 AICallError;不会给空 dict。
    """
    if _cb_should_open(spec.name):
        raise AICallError(f"circuit open for {spec.name}", attempts=0)

    b = _bucket(spec.name, spec.model)
    last_err: str | None = None
    last_status: int | None = None
    last_body: str | None = None
    n_retries = 0
    t_start = time.monotonic()
    text = ""
    parsed: dict = {}
    info: dict = {"attempts": 0, "retries": 0, "tokens": None,
                  "latency_ms": 0, "ttfb_ms": 0, "status": None}

    # R6: 占用全局 inflight slot — 防止 score_batch + stock_ai_analysis 并发把上游冲挂
    with _inflight_ctx():
        n = len(spec.attempts)
        for idx in range(n):
            info["attempts"] = idx + 1
            body = dict(spec.body)
            if idx < len(spec.max_tokens_alts):
                body["max_tokens"] = spec.max_tokens_alts[idx]
            else:
                body["max_tokens"] = spec.fallback_max_tokens
            try:
                t_req = time.monotonic()
                r = _requests.post(spec.url, json=body, headers=spec.headers, timeout=spec.timeout)
                last_status = r.status_code
                # R-sec-007: 先清洗再存 — 上游错误体里会回带 request headers 完整片段
                # (包括 Authorization: Bearer sk-xxx),错误如果直接进 last_err 会被 throw 到
                # 上层日志/前端 toast → 泄露 API key
                last_body = _scrub_secrets(r.text)
                info["status"] = r.status_code
                info["ttfb_ms"] = round((time.monotonic() - t_req) * 1000, 1)

                if r.status_code == 200:
                    j = r.json() or {}
                    if not isinstance(j, dict):
                        last_err = f"unexpected json type: {type(j).__name__}"
                        log.warning(f"AI {last_err} name={spec.name} attempt={idx+1}")
                        n_retries += 1
                        continue
                    choice = (j.get("choices") or [{}])[0] or {}
                    msg = choice.get("message") or {}
                    content = msg.get("content") or ""
                    finish = choice.get("finish_reason", "?")
                    usage = j.get("usage") or {}
                    info["tokens"] = usage.get("total_tokens")
                    if content.strip():
                        text = content
                        break
                    log.warning(f"AI empty content name={spec.name} attempt={idx+1} finish={finish}")
                    last_err = f"empty content (finish={finish})"
                    n_retries += 1
                elif r.status_code in spec.retry_on_status and idx < n - 1:
                    wait_s = min(2 ** idx, 4)
                    log.warning(f"AI retry name={spec.name} status={r.status_code} in {wait_s}s")
                    time.sleep(wait_s)
                    n_retries += 1
                    last_err = f"HTTP {r.status_code}"
                    continue
                else:
                    last_err = f"HTTP {r.status_code}: {_scrub_secrets(r.text)[:200]}"
                    break
            except _requests.exceptions.ReadTimeout as e:
                last_err = f"ReadTimeout: {e}"
                log.warning(f"AI timeout name={spec.name} attempt={idx+1}: {e}")
                if idx < n - 1:
                    n_retries += 1
                    continue
                break
            except _requests.exceptions.ConnectionError as e:
                last_err = f"ConnectionError: {e}"
                if idx < n - 1:
                    time.sleep(0.5 * (idx + 1))
                    n_retries += 1
                    continue
                break
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                log.warning(f"AI exception name={spec.name} attempt={idx+1}: {e}")
                break

        info["latency_ms"] = round((time.monotonic() - t_start) * 1000, 1)

        # R7: 累计 token + 成本估算
        _tok = info.get("tokens") or 0
        if _tok:
            try:
                b.total_tokens   += int(_tok)
                b.total_cost_usd += _estimate_cost_usd(b.model, int(_tok))
            except Exception:
                pass

        if not text:
            _record_metric(b, ok=False, latency_ms=info["latency_ms"], retry=n_retries)
            _cb_record_fail(spec.name)
            raise AICallError(f"AI failed ({last_err})", status=last_status,
                              attempts=info["attempts"], last_body=last_body)

        parsed = parse_json_loose(text)
        _cb_record_ok(spec.name)
        _record_metric(b, ok=True, latency_ms=info["latency_ms"], retry=n_retries,
                       parse_fail=1 if not parsed else 0)
        return text, parsed, info


# ───────────────────────────────────────────────────────────
# 高阶封装 — 给具体调用点用
# ───────────────────────────────────────────────────────────
def headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }


def default_url() -> str:
    return os.environ.get("MINIMAX_BASE_URL",
                         "https://api.minimaxi.com/v1/text/chatcompletion_v2")


def default_model() -> str:
    return os.environ.get("MINIMAX_MODEL", "MiniMax-M3")


# R7: 批量并发调用 (限并发,避免打挂上游)
async def call_batch(specs: list, max_concurrent: int = 4, return_exceptions: bool = True):
    """并发跑多个 CallSpec,返回 [(text, parsed, info) | Exception] 同序列表。

    用法:
        specs = [build_spec(s) for s in items]
        results = await ai_client.call_batch(specs, max_concurrent=3)
        for item, res in zip(items, results):
            if isinstance(res, Exception): ...
    """
    import asyncio
    sem = asyncio.Semaphore(max_concurrent)
    async def _one(spec):
        async with sem:
            try:
                # call() 是同步,丢到默认 executor 避免阻塞事件循环
                return await asyncio.get_running_loop().run_in_executor(None, call, spec)
            except Exception as e:
                if return_exceptions:
                    return e
                raise
    return await asyncio.gather(*[_one(s) for s in specs])
