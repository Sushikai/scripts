#!/usr/bin/env python3
"""
ai_client.call() 网络层 mock 单元测试 (Phase 1)

覆盖 20 场景:
  - happy path, retry/backoff, circuit breaker, inflight
  - secret scrubbing, 4xx/5xx, timeout, ConnectionError
  - empty content, non-dict JSON, metrics, error attributes

运行: pytest tests/test_ai_client_call.py -v
Mock requests.post — 纯离线,无网络依赖。
"""
import json
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT))

from web import ai_client
from web.ai_client import (
    CallSpec, AICallError, call, headers, default_url, default_model,
    _BUCKETS, _CB, _CB_LOCK, _AI_INFLIGHT, _AI_INFLIGHT_MAX, _BUCKETS_LOCK,
)


# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════

def _make_resp(status=200, content="", json_body=None):
    """Factory: 造一个带 status/json/text 的 mock response。"""
    m = mock.MagicMock()
    m.status_code = status
    m.text = content
    if json_body is not None:
        m.json.return_value = json_body
    else:
        m.json.side_effect = json.JSONDecodeError("bad", "", 0)
    return m


def _ok_json(verdict="买", conviction=80):
    """标准成功 JSON 响应."""
    return {
        "choices": [{
            "message": {"content": json.dumps({"verdict": verdict, "conviction": conviction}, ensure_ascii=False)},
            "finish_reason": "stop",
        }],
        "usage": {"total_tokens": 1200},
    }


def _spec(name="test", timeout=10.0, attempts=(1, 2), max_tokens_alts=None, fallback_max_tokens=4000):
    kw = dict(
        url="http://mock/api",
        headers={"Authorization": "Bearer sk-test123"},
        body={"model": "MiniMax-M3", "messages": [{"role": "user", "content": "hello"}]},
        name=name,
        model="MiniMax-M3",
        timeout=timeout,
        attempts=attempts,
    )
    if max_tokens_alts is not None:
        kw["max_tokens_alts"] = max_tokens_alts
    kw["fallback_max_tokens"] = fallback_max_tokens
    return CallSpec(**kw)


# ═══════════════════════════════════════════════════════════
# fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def reset_ai_state():
    """每个测试前重置全局 buckets / circuit breakers / inflight。"""
    with _BUCKETS_LOCK:
        _BUCKETS.clear()
    with _CB_LOCK:
        _CB.clear()
    # 重置 inflight counter (不持锁直接写,单线程测试安全)
    import web.ai_client as _ac
    _ac._AI_INFLIGHT = 0
    yield
    with _BUCKETS_LOCK:
        _BUCKETS.clear()
    with _CB_LOCK:
        _CB.clear()
    _ac._AI_INFLIGHT = 0


# ═══════════════════════════════════════════════════════════
# 1. Happy path
# ═══════════════════════════════════════════════════════════

def test_call_happy_path():
    """200 + 有效 JSON → 返回 (text, parsed, info)。"""
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.return_value = _make_resp(200, json_body=_ok_json())

        text, parsed, info = call(_spec())

    assert parsed["verdict"] == "买"
    assert parsed["conviction"] == 80
    assert info["attempts"] == 1
    assert info["status"] == 200
    assert info["tokens"] == 1200
    assert info["latency_ms"] >= 0


def test_call_info_dict_keys():
    """info 必须包含所有标准字段。"""
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.return_value = _make_resp(200, json_body=_ok_json())

        _, _, info = call(_spec())

    for k in ("attempts", "retries", "tokens", "latency_ms", "ttfb_ms", "status"):
        assert k in info, f"info 缺少字段 {k}"


# ═══════════════════════════════════════════════════════════
# 2. Retry on 429/5xx then success
# ═══════════════════════════════════════════════════════════

def test_call_retry_on_429_then_success():
    """第一次 429 → retry → 第二次 200 成功。"""
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.side_effect = [
            _make_resp(429, "rate limited"),
            _make_resp(200, json_body=_ok_json()),
        ]

        text, parsed, info = call(_spec(attempts=(1, 2)))

    assert parsed["verdict"] == "买"
    assert info["attempts"] == 2
    assert mp.call_count == 2


def test_call_retry_on_503():
    """503 Service Unavailable → 应重试。"""
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.side_effect = [
            _make_resp(503, "unavailable"),
            _make_resp(200, json_body=_ok_json()),
        ]

        _, parsed, _ = call(_spec(attempts=(1, 2)))

    assert parsed["verdict"] == "买"
    assert mp.call_count == 2


def test_call_retry_exhausted():
    """3 次全部 500 → 抛 AICallError。"""
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.side_effect = [
            _make_resp(500, "internal error"),
            _make_resp(500, "internal error"),
            _make_resp(500, "internal error"),
        ]

        with pytest.raises(AICallError, match="HTTP 500"):
            call(_spec(attempts=(1, 2, 3)))

    assert mp.call_count == 3


# ═══════════════════════════════════════════════════════════
# 3. 4xx non-retryable
# ═══════════════════════════════════════════════════════════

def test_call_400_no_retry():
    """400 Bad Request → 立即 fail,不 retry。"""
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.return_value = _make_resp(400, '{"error":"bad request"}')

        with pytest.raises(AICallError, match="HTTP 400"):
            call(_spec(attempts=(1, 2)))

    assert mp.call_count == 1  # 不重试


def test_call_401_no_retry():
    """401 Unauthorized → 立即 fail。"""
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.return_value = _make_resp(401, "unauthorized")

        with pytest.raises(AICallError, match="HTTP 401"):
            call(_spec(attempts=(1, 2)))

    assert mp.call_count == 1


# ═══════════════════════════════════════════════════════════
# 4. Empty content / unexpected JSON
# ═══════════════════════════════════════════════════════════

def test_call_empty_content_retry():
    """200 但 content 为空 → retry。"""
    empty = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}], "usage": {}}
    ok = _ok_json()
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.side_effect = [
            _make_resp(200, json_body=empty),
            _make_resp(200, json_body=ok),
        ]

        _, parsed, info = call(_spec(attempts=(1, 2)))

    assert parsed["verdict"] == "买"
    assert info["attempts"] == 2


def test_call_non_dict_json_response():
    """200 但 json() 返回 list 而非 dict → retry 然后成功。"""
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.side_effect = [
            _make_resp(200, json_body=["not", "a", "dict"]),
            _make_resp(200, json_body=_ok_json()),
        ]

        _, parsed, _ = call(_spec(attempts=(1, 2)))

    assert parsed["verdict"] == "买"
    assert mp.call_count == 2


# ═══════════════════════════════════════════════════════════
# 5. Timeout / ConnectionError
# ═══════════════════════════════════════════════════════════

def test_call_read_timeout():
    """ReadTimeout → retry if attempts remain。"""
    import requests as _r
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.side_effect = [
            _r.exceptions.ReadTimeout("read timed out"),
            _make_resp(200, json_body=_ok_json()),
        ]

        _, parsed, _ = call(_spec(attempts=(1, 2)))

    assert parsed["verdict"] == "买"
    assert mp.call_count == 2


def test_call_read_timeout_exhausted():
    """ReadTimeout on all attempts → AICallError。"""
    import requests as _r
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.side_effect = _r.exceptions.ReadTimeout("read timed out")

        with pytest.raises(AICallError, match="ReadTimeout"):
            call(_spec(attempts=(1, 2)))

    assert mp.call_count == 2


def test_call_connection_error_retry():
    """ConnectionError → retry with backoff。"""
    import requests as _r
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.side_effect = [
            _r.exceptions.ConnectionError("refused"),
            _make_resp(200, json_body=_ok_json()),
        ]

        _, parsed, _ = call(_spec(attempts=(1, 2)))

    assert parsed["verdict"] == "买"
    assert mp.call_count == 2


# ═══════════════════════════════════════════════════════════
# 6. Circuit breaker
# ═══════════════════════════════════════════════════════════

def test_circuit_breaker_opens_after_4_failures():
    """同一 name 连续 4 次失败 → 第 5 次抛 circuit open。"""
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.return_value = _make_resp(500, "boom")

        for _ in range(4):
            try:
                call(_spec(name="cb-test"))
            except AICallError:
                pass

        # 第 5 次应直接被熔断
        with pytest.raises(AICallError, match="circuit open"):
            call(_spec(name="cb-test"))


def test_circuit_breaker_resets_on_success():
    """成功调用重置 fail streak,不会触发熔断。"""
    with mock.patch("web.ai_client._requests.post") as mp:
        # 3 次 fail (不够阈值)
        mp.return_value = _make_resp(500, "err")
        for _ in range(3):
            try:
                call(_spec(name="cb-reset"))
            except AICallError:
                pass

        # 1 次成功 → reset
        mp.return_value = _make_resp(200, json_body=_ok_json())
        call(_spec(name="cb-reset"))

        # 现在 fail streak 被 reset,可以再 fail 3 次不出 circuit open
        mp.return_value = _make_resp(500, "err")
        for _ in range(3):
            try:
                call(_spec(name="cb-reset"))
            except AICallError:
                pass

        # 第 4 次才 open (总共 fail 4,但中间被 reset 过了)
        with pytest.raises(AICallError, match="circuit open"):
            call(_spec(name="cb-reset"))


def test_circuit_breaker_per_name_isolation():
    """不同 name 的熔断器互相独立。"""
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.return_value = _make_resp(500, "boom")
        for _ in range(4):
            try:
                call(_spec(name="isolated-A"))
            except AICallError:
                pass

    # name A 被熔断
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.return_value = _make_resp(200, json_body=_ok_json())
        # name B 应不受影响
        _, parsed, _ = call(_spec(name="isolated-B"))
        assert parsed["verdict"] == "买"


# ═══════════════════════════════════════════════════════════
# 7. Secret scrubbing
# ═══════════════════════════════════════════════════════════

def test_secret_scrubbing_in_error_body():
    """错误响应体里的 API key 应被清洗。"""
    leaky_body = '{"error":"auth failed","auth":"Bearer sk-deadbeef12345678"}'
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.return_value = _make_resp(401, leaky_body)

        try:
            call(_spec())
        except AICallError as e:
            assert "sk-deadbeef" not in str(e.last_body)
            assert "sk-deadbeef" not in str(e)
            assert "REDACTED" in str(e.last_body) or "sk-[REDACTED]" in str(e.last_body)


def test_secret_scrubbing_api_key_header():
    """错误体里含 x-api-key 应被清洗。"""
    leaky = '{"x-api-key":"sk-1234567890abcdef"}'
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.return_value = _make_resp(401, leaky)

        try:
            call(_spec())
        except AICallError as e:
            assert "sk-1234567890abcdef" not in e.last_body


# ═══════════════════════════════════════════════════════════
# 8. AICallError attributes
# ═══════════════════════════════════════════════════════════

def test_aicall_error_has_status():
    """AICallError 应携带 HTTP status。"""
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.return_value = _make_resp(400, "bad request")

        try:
            call(_spec())
        except AICallError as e:
            assert e.status == 400
            assert e.attempts == 1


def test_aicall_error_attempts_count():
    """多次 retry 后 AICallError.attempts 应为总次数。"""
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.return_value = _make_resp(500, "err")

        try:
            call(_spec(attempts=(1, 2, 3)))
        except AICallError as e:
            assert e.attempts == 3


# ═══════════════════════════════════════════════════════════
# 9. Bucket metrics
# ═══════════════════════════════════════════════════════════

def test_bucket_metrics_ok_increments():
    """成功后 bucket ok/calls 递增。"""
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.return_value = _make_resp(200, json_body=_ok_json())
        call(_spec(name="metrics-test"))

    b = ai_client._bucket("metrics-test", "any")
    assert b.ok == 1
    assert b.calls == 1
    assert b.fail == 0


def test_bucket_metrics_fail_increments():
    """失败后 bucket fail/calls 递增。"""
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.return_value = _make_resp(500, "err")
        try:
            call(_spec(name="metrics-fail"))
        except AICallError:
            pass

    b = ai_client._bucket("metrics-fail", "any")
    assert b.fail == 1
    assert b.calls == 1
    assert b.ok == 0


def test_bucket_tracks_tokens():
    """成功后 total_tokens 递增。"""
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.return_value = _make_resp(200, json_body=_ok_json())
        call(_spec(name="tokens-test"))

    b = ai_client._bucket("tokens-test", "any")
    assert b.total_tokens == 1200


# ═══════════════════════════════════════════════════════════
# 10. Multiple attempt token sizes
# ═══════════════════════════════════════════════════════════

def test_call_different_max_tokens_per_attempt():
    """每次 attempt 使用对应的 max_tokens_alts。"""
    sent_bodies = []
    with mock.patch("web.ai_client._requests.post") as mp:
        def _capture(url, json, headers, timeout):
            sent_bodies.append(dict(json))
            return _make_resp(200, json_body=_ok_json())
        mp.side_effect = _capture

        call(_spec(attempts=(1, 2), max_tokens_alts=(100, 200)))

    assert sent_bodies[0]["max_tokens"] == 100
    # 第一次就成功了,所以只有 1 个 body


def test_call_fallback_max_tokens():
    """超过 max_tokens_alts 长度时使用 fallback。"""
    sent_bodies = []
    with mock.patch("web.ai_client._requests.post") as mp:
        def _capture(url, json, headers, timeout):
            sent_bodies.append(dict(json))
            return _make_resp(500, "err")  # 每次失败触发 retry
        mp.side_effect = _capture

        try:
            call(_spec(attempts=(1, 2, 3), max_tokens_alts=(100,), fallback_max_tokens=999))
        except AICallError:
            pass

    assert sent_bodies[0]["max_tokens"] == 100
    assert sent_bodies[1]["max_tokens"] == 999
    assert sent_bodies[2]["max_tokens"] == 999


# ═══════════════════════════════════════════════════════════
# 11. helpers
# ═══════════════════════════════════════════════════════════

def test_headers_format():
    h = headers("sk-test123")
    assert h["Authorization"] == "Bearer sk-test123"
    assert h["Content-Type"] == "application/json"


def test_default_model_from_env(monkeypatch):
    monkeypatch.setenv("MINIMAX_MODEL", "claude-sonnet-4-6")
    assert default_model() == "claude-sonnet-4-6"


def test_default_url_default():
    assert "minimaxi.com" in default_url() or "api" in default_url()


# ═══════════════════════════════════════════════════════════
# 12. parse_json_loose integration via call()
# ═══════════════════════════════════════════════════════════

def test_call_parses_markdown_fence():
    """call() 内部用 parse_json_loose,应能解析 markdown fence 包裹的 JSON。"""
    inner = '{"verdict":"买","conviction":90}'
    fenced = f"```json\n{inner}\n```"
    body = {
        "choices": [{"message": {"content": fenced}, "finish_reason": "stop"}],
        "usage": {},
    }
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.return_value = _make_resp(200, json_body=body)

        _, parsed, _ = call(_spec())

    assert parsed["verdict"] == "买"
    assert parsed["conviction"] == 90


# ═══════════════════════════════════════════════════════════
# 13. Edge cases
# ═══════════════════════════════════════════════════════════

def test_call_unknown_exception_no_retry():
    """非网络异常 (如 ValueError) → 不 retry,直接 fail。"""
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.side_effect = ValueError("unexpected")

        with pytest.raises(AICallError, match="ValueError"):
            call(_spec(attempts=(1, 2)))

    assert mp.call_count == 1


def test_call_spec_retry_on_status_default():
    """默认 retry_on_status 包含 429/500/502/503/504。"""
    s = _spec()
    assert 429 in s.retry_on_status
    assert 500 in s.retry_on_status
    assert 502 in s.retry_on_status


def test_call_with_no_tokens_in_response():
    """usage 字段缺失 → tokens=None,不影响成功返回。"""
    body = {
        "choices": [{"message": {"content": '{"verdict":"观望","conviction":50}'}, "finish_reason": "stop"}],
    }
    with mock.patch("web.ai_client._requests.post") as mp:
        mp.return_value = _make_resp(200, json_body=body)

        _, parsed, info = call(_spec())

    assert parsed["verdict"] == "观望"
    assert info["tokens"] is None
