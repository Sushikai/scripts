#!/usr/bin/env python3
"""
AI review e2e 测试 (Phase 3c)

Mock ai_client.call / _build_context / DB.
覆盖: review_trade LLM 调用, fallback, cache hit, verdict 归一化, SSE 事件流.

运行: pytest tests/test_ai_review_e2e.py -v
"""
import json
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from tuixue_v3.web import review as _review_mod
from tuixue_v3.web import server as _srv_mod


# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════

def _mock_trade():
    return {
        "id": 1, "code": "600519", "name": "测试股", "direction": "买入",
        "price": 1800.0, "shares": 100, "occurred_at": "2026-07-28 10:30:00",
        "memo": "", "tags": "",
    }


def _mock_ai_review_response():
    return (
        json.dumps({
            "verdict": "优", "score": 85, "summary": "操作果断,买点准确",
            "main_mistake": "", "mistake_pattern": "无", "improvement": "继续保持",
            "ai_advice": "下次注意量比", "taxonomy_role": "main", "is_mainline": True,
            "limit_up_recap": "",
        }),
        {"verdict": "优", "score": 85},
        {"attempts": 1, "tokens": 1200, "latency_ms": 800},
    )


# ═══════════════════════════════════════════════════════════
# 1. review_trade — AI call path
# ═══════════════════════════════════════════════════════════

def test_review_trade_ai_call_ok(monkeypatch):
    """Mock 完整的 review_trade 流程: DB + context + AI call."""
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
    trade = _mock_trade()

    with mock.patch("tuixue_v3.web.review.get_trade", return_value=trade), \
         mock.patch("tuixue_v3.web.review._build_context", return_value={"quote": {}, "kline": []}), \
         mock.patch("tuixue_v3.web.review._memory_context", return_value=""), \
         mock.patch("tuixue_v3.web.review._format_ctx_for_ai", return_value=("sys", "user")), \
         mock.patch("tuixue_v3.web.ai_client.call") as mock_call, \
         mock.patch("tuixue_v3.web.review._conn"), \
         mock.patch("tuixue_v3.web.review._safe_write"):
        mock_call.return_value = _mock_ai_review_response()

        # 无缓存 → 走 AI
        mock_conn = mock.MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None  # no existing review
        with mock.patch("tuixue_v3.web.review._conn", return_value=mock_conn):
            result = _review_mod.review_trade(1, force=True)

        assert result is not None
        assert result.get("verdict") == "优"
        mock_call.assert_called_once()


def test_review_trade_cache_hit():
    """已有复盘记录时直接返回,不调 AI。"""
    trade = _mock_trade()
    existing_row = (1, "及格", 60, "摘要", "[]", "[]", "追高", "等回调", "[]", "{}", time.time())

    mock_conn = mock.MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = existing_row

    with mock.patch("tuixue_v3.web.review.get_trade", return_value=trade), \
         mock.patch("tuixue_v3.web.review._conn", return_value=mock_conn), \
         mock.patch("tuixue_v3.web.ai_client.call") as mock_call:
        result = _review_mod.review_trade(1, force=False)

        assert result is not None
        assert result.get("verdict") == "及格"
        mock_call.assert_not_called()


def test_review_trade_no_api_key_fallback(monkeypatch):
    """无 API key 时走 fallback 评分。"""
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    trade = _mock_trade()

    mock_conn = mock.MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = None

    with mock.patch("tuixue_v3.web.review.get_trade", return_value=trade), \
         mock.patch("tuixue_v3.web.review._build_context", return_value={"quote": {}, "kline": []}), \
         mock.patch("tuixue_v3.web.review._memory_context", return_value=""), \
         mock.patch("tuixue_v3.web.review._format_ctx_for_ai", return_value=("sys", "user")), \
         mock.patch("tuixue_v3.web.review._conn", return_value=mock_conn), \
         mock.patch("tuixue_v3.web.review._safe_write"):
        result = _review_mod.review_trade(1, force=True)

        assert result is not None
        # fallback review 有基本字段
        assert "verdict" in result
        assert "score" in result


def test_review_trade_ai_error_fallback(monkeypatch):
    """AI 调用失败时走 fallback。"""
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
    trade = _mock_trade()
    from tuixue_v3.web.ai_client import AICallError

    mock_conn = mock.MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = None

    with mock.patch("tuixue_v3.web.review.get_trade", return_value=trade), \
         mock.patch("tuixue_v3.web.review._build_context", return_value={"quote": {}, "kline": []}), \
         mock.patch("tuixue_v3.web.review._memory_context", return_value=""), \
         mock.patch("tuixue_v3.web.review._format_ctx_for_ai", return_value=("sys", "user")), \
         mock.patch("tuixue_v3.web.review._conn", return_value=mock_conn), \
         mock.patch("tuixue_v3.web.review._safe_write"), \
         mock.patch("tuixue_v3.web.ai_client.call", side_effect=AICallError("Service Unavailable", status=503, attempts=2)):
        result = _review_mod.review_trade(1, force=True)

        assert result is not None
        assert "verdict" in result


def test_review_trade_taxonomy_noise_downgrade(monkeypatch):
    """杂毛(noise)角色时 verdict 不优于 '及格'。"""
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
    trade = _mock_trade()

    noise_response = (
        json.dumps({
            "verdict": "优", "score": 80, "summary": "操作尚可",
            "main_mistake": "", "mistake_pattern": "", "improvement": "",
            "ai_advice": "", "taxonomy_role": "noise", "is_mainline": False,
            "limit_up_recap": "",
        }),
        {"verdict": "优", "score": 80, "taxonomy_role": "noise"},
        {"attempts": 1},
    )

    mock_conn = mock.MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = None

    with mock.patch("tuixue_v3.web.review.get_trade", return_value=trade), \
         mock.patch("tuixue_v3.web.review._build_context", return_value={"quote": {}, "kline": []}), \
         mock.patch("tuixue_v3.web.review._memory_context", return_value=""), \
         mock.patch("tuixue_v3.web.review._format_ctx_for_ai", return_value=("sys", "user")), \
         mock.patch("tuixue_v3.web.review._conn", return_value=mock_conn), \
         mock.patch("tuixue_v3.web.review._safe_write"), \
         mock.patch("tuixue_v3.web.ai_client.call", return_value=noise_response):
        result = _review_mod.review_trade(1, force=True)

        # 杂毛 → verdict 强制不优于"及格"
        assert result.get("verdict") == "及格"


# ═══════════════════════════════════════════════════════════
# 2. SSE endpoint — stream/review/{trade_id}
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_sse_review_start_event(monkeypatch):
    """SSE review 端点返回 start 事件。"""
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
    from fastapi.testclient import TestClient
    from tuixue_v3.web.server import app

    client = TestClient(app)

    trade = _mock_trade()
    review_result = {
        "verdict": "优", "score": 85, "summary_md": "操作果断",
        "rules_passed": ["#1 买点准确"], "rules_failed": [],
        "key_risks": [], "main_mistake": "", "improvement": "",
        "taxonomy_role": "main", "is_mainline": True,
        "limit_up_recap": "", "mistake_pattern": "",
    }

    with mock.patch("tuixue_v3.web.server._check_sse_origin", return_value=None), \
         mock.patch("tuixue_v3.web.server._review.review_trade", return_value=review_result):
        # SSE 响应通过 TestClient 流式读取
        with client.stream("GET", "/api/stream/review/1") as response:
            assert response.status_code == 200
            # 读取前几个事件
            lines = []
            for _ in range(20):
                chunk = response.iter_lines()
                for line in chunk:
                    if line:
                        lines.append(line)
                if any("done" in l for l in lines):
                    break
            assert any("start" in l for l in lines)
            assert any("done" in l for l in lines)


# ═══════════════════════════════════════════════════════════
# 3. verdict / score 白名单校验
# ═══════════════════════════════════════════════════════════

def test_verdict_normalization_in_ai_parse():
    """review_trade 在 AI 返回非法 verdict 时归一化为 '及格'。"""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
    trade = _mock_trade()

    # AI 返回非法 verdict
    bad_response = (
        json.dumps({
            "verdict": "invalid_verdict", "score": 70, "summary": "摘要",
            "main_mistake": "", "mistake_pattern": "", "improvement": "",
            "ai_advice": "", "taxonomy_role": "main", "is_mainline": True,
            "limit_up_recap": "",
        }),
        {"verdict": "invalid_verdict", "score": 70},
        {"attempts": 1},
    )

    mock_conn = mock.MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = None

    with mock.patch("tuixue_v3.web.review.get_trade", return_value=trade), \
         mock.patch("tuixue_v3.web.review._build_context", return_value={"quote": {}, "kline": []}), \
         mock.patch("tuixue_v3.web.review._memory_context", return_value=""), \
         mock.patch("tuixue_v3.web.review._format_ctx_for_ai", return_value=("sys", "user")), \
         mock.patch("tuixue_v3.web.review._conn", return_value=mock_conn), \
         mock.patch("tuixue_v3.web.review._safe_write"), \
         mock.patch("tuixue_v3.web.ai_client.call", return_value=bad_response):
        result = _review_mod.review_trade(1, force=True)

    # 白名单外 → review_trade 内归一化为 "及格"
    assert result["verdict"] == "及格"


def test_review_row_to_dict_shape():
    """_review_row_to_dict 返回完整结构。"""
    trade = _mock_trade()
    row = (1, "优", 85, "摘要内容", '[{"id":"#1"}]', '[{"id":"#2"}]',
           "追高", "等回调", '["风险1"]', '{"kline":[]}', time.time())

    result = _review_mod._review_row_to_dict(row, trade)

    assert result["verdict"] == "优"
    assert result["score"] == 85
    assert result["summary"] == "摘要内容"
    assert len(result["rules_passed"]) == 1
    assert len(result["rules_failed"]) == 1
    assert result["key_risks"] == ["风险1"]
    assert result["trade"]["code"] == "600519"
    assert result["trade"]["direction"] == "买入"
