"""flow 统一 AI 客户端:重试 / 熔断 / 注入防御 / Schema 兜底 / 截断。

借鉴 tuixue_v3/web/ai_client.py 的设计但独立实现,避免双维护。

用法:
    from backend.ai.client import call_llm
    out = call_llm("claude-haiku-4-5", "system: 你是个编剧...", "写一个 60 秒脚本,主题 AI")
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .. import _constants as C

_logger = logging.getLogger("flow.ai")


# === 注入防御:把用户内容包在 boundary 里 ===
_USER_MSG_OPEN = "<user_msg>"
_USER_MSG_CLOSE = "</user_msg>"
_HISTORY_OPEN = "<history>"
_HISTORY_CLOSE = "</history>"


def wrap_user_msg(content: str, max_chars: int = 8000) -> str:
    """用户内容包 boundary,防 prompt 注入。截断避免超长。"""
    body = cap_text(content, max_chars)
    return f"{_USER_MSG_OPEN}{body}{_USER_MSG_CLOSE}"


def wrap_history(messages: list[dict]) -> str:
    """多轮历史包 boundary。"""
    out = []
    for m in messages:
        role = m.get("role", "user")
        text = cap_text(m.get("content", ""), 4000)
        out.append(f"[{role}] {text}")
    return _HISTORY_OPEN + "\n".join(out) + _HISTORY_CLOSE


def cap_text(s: str, max_chars: int) -> str:
    if not s:
        return ""
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 50] + "...[truncated]"


# === JSON 安全解析 ===
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(previous|above)\s+(instructions?|prompts?)", re.I),
    re.compile(r"system\s*:\s*", re.I),
    re.compile(r"</?(system|user|assistant|history)>", re.I),
]


def sanitize_for_json(obj: Any) -> Any:
    """处理 NaN/Inf + 不可序列化对象。"""
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_for_json(x) for x in obj]
    return obj


def json_dumps_safe(obj: Any) -> str:
    return json.dumps(sanitize_for_json(obj), ensure_ascii=False)


def parse_json_loose(text: str) -> Optional[Any]:
    """从 LLM 输出捞 JSON:支持 ```json 围栏 + 裸对象/数组。"""
    if not text:
        return None
    # 1) ```json ... ```
    m = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 2) 找第一个 { 或 [ 到对应闭合
    obj_start = text.find("{")
    arr_start = text.find("[")
    candidates = []
    if obj_start >= 0 and "}" in text:
        candidates.append((obj_start, text.rindex("}") + 1, "{"))
    if arr_start >= 0 and "]" in text:
        candidates.append((arr_start, text.rindex("]") + 1, "["))
    candidates.sort(key=lambda x: x[0])
    for s, e, _ in candidates:
        snippet = text[s:e]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            continue
    return None


# === 注入检测(只读不阻断,日志记录) ===
def detect_injection(text: str) -> bool:
    for pat in _INJECTION_PATTERNS:
        if pat.search(text):
            return True
    return False


# === 熔断器 ===
@dataclass
class CircuitBreaker:
    fail_threshold: int = 5
    reset_sec: int = 60
    _fail_count: int = 0
    _opened_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def should_open(self) -> bool:
        with self._lock:
            if self._opened_at > 0:
                # 已打开,看是否到 reset 时间
                if time.time() - self._opened_at > self.reset_sec:
                    # 半开重置
                    self._fail_count = 0
                    self._opened_at = 0
                    return False
                return True
            return self._fail_count >= self.fail_threshold

    def record_success(self) -> None:
        with self._lock:
            self._fail_count = max(0, self._fail_count - 1)
            if self._fail_count == 0:
                self._opened_at = 0

    def record_failure(self) -> None:
        with self._lock:
            self._fail_count += 1
            if self._fail_count >= self.fail_threshold and self._opened_at == 0:
                self._opened_at = time.time()


# === 调用统计 ===
@dataclass
class _Bucket:
    ok: int = 0
    fail: int = 0
    retries: int = 0
    schema_fail: int = 0
    injection_caught: int = 0


_STATS = _Bucket()
_BREAKER = CircuitBreaker(fail_threshold=C.AI_CB_FAIL_THRESHOLD, reset_sec=C.AI_CB_RESET_SEC)


def stats() -> dict:
    return {
        "ok": _STATS.ok,
        "fail": _STATS.fail,
        "retries": _STATS.retries,
        "schema_fail": _STATS.schema_fail,
        "injection_caught": _STATS.injection_caught,
        "breaker_open": _BREAKER.should_open(),
    }


# === 单 inflight 去重 ===
_INFLIGHT: dict[str, Any] = {}
_INFLIGHT_LOCK = threading.Lock()


def _inflight_acquire(key: str) -> bool:
    with _INFLIGHT_LOCK:
        if key in _INFLIGHT:
            return False
        _INFLIGHT[key] = True
        return True


def _inflight_release(key: str) -> None:
    with _INFLIGHT_LOCK:
        _INFLIGHT.pop(key, None)


# === 实际调用层(由 provider 模块注入) ===
_PROVIDER: Optional[Callable[..., str]] = None


def set_provider(fn: Callable[..., str]) -> None:
    """注入 provider:signature (model, system, user, **kw) -> str。"""
    global _PROVIDER
    _PROVIDER = fn


def call_llm(
    model: str,
    system: str,
    user: str,
    *,
    timeout: float = 30.0,
    expect_json: bool = False,
    cache_key: str | None = None,
    max_retries: int = C.AI_RETRY_MAX,
    provider_kwargs: dict | None = None,
) -> dict:
    """统一调用入口。

    Returns: {"text": str, "parsed": dict|None, "retries": int, "from_cache": bool}
    """
    if _BREAKER.should_open():
        _STATS.fail += 1
        raise RuntimeError("circuit_breaker_open")

    # 注入检测
    if detect_injection(user):
        _STATS.injection_caught += 1
        _logger.warning("injection pattern detected in user content")

    wrapped_user = wrap_user_msg(user)

    # 缓存检查
    if cache_key:
        from ..cache.store import get as cache_get, set_ as cache_set
        hit = cache_get(f"flow:ai:{cache_key}")
        if hit is not None:
            _STATS.ok += 1
            return {**hit, "from_cache": True}

    # inflight 去重
    inflight_key = cache_key or f"{model}:{hash(user) & 0xffffffff}"
    if not _inflight_acquire(inflight_key):
        # 简单处理:等一下复用
        time.sleep(0.05)
        if cache_key:
            hit = cache_get(f"flow:ai:{cache_key}")
            if hit:
                _inflight_release(inflight_key)
                _STATS.ok += 1
                return {**hit, "from_cache": True}

    try:
        last_err: Exception | None = None
        for attempt in range(max_retries):
            try:
                if _PROVIDER is None:
                    raise RuntimeError("no provider set; call set_provider() first")
                t0 = time.perf_counter()
                text = _PROVIDER(
                    model=model,
                    system=system,
                    user=wrapped_user,
                    timeout=timeout,
                    **(provider_kwargs or {}),
                )
                latency = (time.perf_counter() - t0) * 1000
                _logger.info("ai call ok model=%s latency_ms=%.1f retries=%d", model, latency, attempt)
                parsed = None
                if expect_json:
                    parsed = parse_json_loose(text)
                    if parsed is None:
                        _STATS.schema_fail += 1
                        raise ValueError("schema parse failed")
                result = {"text": text, "parsed": parsed, "retries": attempt, "from_cache": False, "latency_ms": latency}
                if cache_key:
                    from ..cache.store import set_ as cache_set
                    cache_set(f"flow:ai:{cache_key}", {k: v for k, v in result.items() if k != "from_cache"}, ttl=600)
                _STATS.ok += 1
                _BREAKER.record_success()
                return result
            except Exception as e:
                last_err = e
                _STATS.retries += 1
                _logger.warning("ai call attempt %d failed: %s", attempt + 1, str(e)[:200])
                time.sleep((C.AI_RETRY_BASE_MS * (2 ** attempt)) / 1000.0)
        _STATS.fail += 1
        _BREAKER.record_failure()
        raise RuntimeError(f"ai call failed after {max_retries} retries: {last_err}")
    finally:
        _inflight_release(inflight_key)