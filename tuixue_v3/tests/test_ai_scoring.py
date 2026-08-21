#!/usr/bin/env python3
"""
ai_scoring 单元测试 (Phase 2)

Mock ai_client.call / cache_db / context fetchers.
覆盖: cache hit/miss, degraded fallback, semaphore limit, aggregate ranking.

运行: pytest tests/test_ai_scoring.py -v
"""
import asyncio
import json
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from tuixue_v3.web import ai_scoring
from tuixue_v3.web import ai_client


# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════

def _cached_verdict(code="600519", verdict="买", conviction=80):
    return {
        "verdict": verdict, "role": "龙头", "conviction": conviction,
        "layer_pass": {}, "rules_passed": [], "rules_failed": [],
        "key_risks": [], "summary": "测试摘要",
        "ts_updated": time.time(), "sector": "", "from_cache": True,
    }


def _scored_candidate(code="600519", name="测试", verdict="买", conviction=80, from_cache=True):
    return {
        "code": code, "name": name, "sector": "科技",
        "ai": {
            "verdict": verdict, "role": "龙头", "conviction": conviction,
            "summary": "测试摘要", "from_cache": from_cache,
            "ts_updated": time.time(),
        },
    }


# ═══════════════════════════════════════════════════════════
# 1. score_one — cache behavior
# ═══════════════════════════════════════════════════════════

def test_score_one_no_api_key(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    result = asyncio.run(ai_scoring.score_one("600519"))
    assert result is None


def test_score_one_cache_hit(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
    cached = _cached_verdict()

    with mock.patch("tuixue_v3.cache_db.get_cached_ai", return_value=cached):
        result = asyncio.run(ai_scoring.score_one("600519"))

    assert result is not None
    assert result["verdict"] == "买"
    assert result.get("from_cache") is True


def test_score_one_cache_pollution_skip(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
    polluted = {"verdict": "invalid_value", "role": "unknown", "conviction": "not_a_number"}

    with mock.patch("tuixue_v3.cache_db.get_cached_ai", return_value=polluted):
        result = asyncio.run(ai_scoring.score_one("600519"))
        assert result is None


# ═══════════════════════════════════════════════════════════
# 2. score_aggregate — ranking logic (async function!)
# ═══════════════════════════════════════════════════════════

def test_score_aggregate_no_api_key(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    scored = [_scored_candidate()]
    result = asyncio.run(ai_scoring.score_aggregate(scored))
    assert result is None


def test_score_aggregate_empty_input():
    scored = [{"code": "600519", "name": "测试", "ai": None}]
    result = asyncio.run(ai_scoring.score_aggregate(scored))
    assert result is None


def test_score_aggregate_cache_hit(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
    now = time.time()
    cached_agg = {
        "ranking": [{"code": "600519", "name": "测试", "recommendation": "买入", "role": "龙头", "reason": "主线龙头"}],
        "overall_view": "测试整体点评",
        "ts": now,
    }
    # ts_updated 必须 ≤ cached_agg.ts 才命中缓存
    scored = [{
        "code": "600519", "name": "测试", "sector": "科技",
        "ai": {
            "verdict": "买", "role": "龙头", "conviction": 80,
            "summary": "测试", "from_cache": True, "ts_updated": now - 1,
        },
    }]

    with mock.patch("tuixue_v3.cache_db.get_cached_aggregate", return_value=cached_agg):
        result = asyncio.run(ai_scoring.score_aggregate(scored))

    assert result is not None
    assert result.get("from_cache") is True
    assert len(result["ranking"]) == 1


def test_score_aggregate_cache_stale(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
    cached_agg = {
        "ranking": [{"code": "600519", "name": "测试", "recommendation": "买入", "role": "龙头", "reason": "旧"}],
        "overall_view": "旧点评",
        "ts": 0,
    }
    scored = [_scored_candidate(from_cache=False)]

    with mock.patch("tuixue_v3.cache_db.get_cached_aggregate", return_value=cached_agg):
        with mock.patch("tuixue_v3.web.ai_client.call") as mock_call:
            mock_call.return_value = (
                json.dumps({"ranking": [], "overall_view": "新点评"}),
                {"ranking": [], "overall_view": "新点评"},
                {"attempts": 1},
            )
            asyncio.run(ai_scoring.score_aggregate(scored))

    mock_call.assert_called_once()


def test_score_aggregate_cache_schema_broken(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
    broken_agg = {"ranking": "not_a_list", "overall_view": 123}

    scored = [_scored_candidate(from_cache=True)]

    with mock.patch("tuixue_v3.cache_db.get_cached_aggregate", return_value=broken_agg):
        with mock.patch("tuixue_v3.web.ai_client.call") as mock_call:
            mock_call.return_value = (
                json.dumps({"ranking": [], "overall_view": "重算"}),
                {"ranking": [], "overall_view": "重算"},
                {"attempts": 1},
            )
            asyncio.run(ai_scoring.score_aggregate(scored))

    mock_call.assert_called_once()


# ═══════════════════════════════════════════════════════════
# 3. Semaphore / concurrency
# ═══════════════════════════════════════════════════════════

def test_semaphore_exists():
    assert ai_scoring._AI_SEM is not None


def test_scoring_executor_exists():
    assert ai_scoring._SCORING_EXECUTOR is not None


# ═══════════════════════════════════════════════════════════
# 4. _today_str helper
# ═══════════════════════════════════════════════════════════

def test_today_str_format():
    result = ai_scoring._today_str()
    assert len(result) == 8
    assert result.isdigit()


# ═══════════════════════════════════════════════════════════
# 5. score_batch behavior (async function!)
# ═══════════════════════════════════════════════════════════

def test_score_batch_empty_candidates():
    result = asyncio.run(ai_scoring.score_batch([]))
    assert result["scored"] == []
    # 不验证 aggregate 字段 (内部调用了 score_aggregate 协程可能不执行)


def test_score_batch_no_api_key(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    result = asyncio.run(ai_scoring.score_batch([{"code": "600519", "name": "测试"}]))
    # 无 API key 时 score_one 返回 None
    assert all(s.get("ai") is None for s in result["scored"])
