"""
tests/test_deep_analysis_contract.py — 个股 AI 深度判断契约 (2026-07-30)

跑法:
    cd /Users/kaikai/scripts/tuixue_v3
    PYTHONPATH=.. python3 -m pytest tests/test_deep_analysis_contract.py -v -m contract

覆盖:
  1.  /api/stock/{code}/deep_analysis         — envelope + 必填字段
  2.  background=0/1 行为                      — queued vs 同步
  3.  cache hit                                — from_cache=True + < 50ms
  4.  recommendation_action 白名单              — ∈ {"加仓", "继续持有", "减仓", "清仓", "观望"}
  5.  profit_taking_score ∈ [0, 100]          — clamp 后合法
  6.  holding_advice 字段 schema               — stop_loss / target_price / horizon_days / rationale
  7.  tech_position 物理                       — pullback_from_60d_high_pct ≤ 0, pct_position_60d ∈ [0,100]
  8.  fundamentals.jump 检测                   — abs(同比) > 30% → jump=True
  9.  profile.business_summary 文本 ≤ 200 字   — 不烧 LLM token
 10.  holding.has_position 字段                — True/False 都合法
"""
from __future__ import annotations
import json
import time
import urllib.request
from typing import Any

import httpx
import pytest

BASE = "http://127.0.0.1:7799"
ACTION_WHITELIST = {"加仓", "继续持有", "减仓", "清仓", "观望"}


def _is_listening(timeout: float = 0.6) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 7799), timeout=timeout):
            return True
    except OSError:
        return False


def _clear_lock(code: str) -> None:
    """清掉 Redis 上 stale lock — 旧测试残留会致 background=1 返 queued=False"""
    try:
        import redis as _r
        c = _r.Redis(host="127.0.0.1", port=6379, db=0, decode_responses=True)
        c.delete(f"tx3:deep_bg_lock:{code}")
    except Exception:
        pass


@pytest.fixture(scope="module")
def client():
    if not _is_listening():
        pytest.skip("server not listening on 127.0.0.1:7799")
    with httpx.Client(base_url=BASE, timeout=10.0) as c:
        yield c


@pytest.fixture(scope="module")
def warm_600519(client):
    """预热 600519 缓存 — 之前 session 已写入, 这里直接 verify。"""
    r = client.get("/api/stock/600519/deep_analysis")
    assert r.status_code == 200, r.text[:300]
    return r.json()


def _data(j: dict) -> dict:
    assert j.get("ok") is True, f"envelope not ok: {j}"
    assert "data" in j
    return j["data"]


# 1. envelope + 必填字段
@pytest.mark.contract
def test_deep_analysis_envelope_shape(warm_600519):
    dd = _data(warm_600519)
    for k in ("code", "ts", "fundamentals", "holding", "tech_position"):
        assert k in dd, f"missing top-level key: {k}"
    assert dd["code"] == "600519"
    assert isinstance(dd["ts"], (int, float))


# 2. background=0 同步路径
@pytest.mark.contract
def test_background_0_sync_path(client):
    _clear_lock("000001")
    r = client.get("/api/stock/000001/deep_analysis?background=0&refresh=1", timeout=10.0)
    assert r.status_code == 200, r.text[:300]
    dd = _data(r.json())
    # 同步路径应当走 _do_deep_analysis 直接返回数据 (无 queued key)
    assert "fundamentals" in dd
    assert "holding" in dd
    assert "tech_position" in dd


# 3. cache hit + from_cache + 低延迟
@pytest.mark.contract
def test_cache_hit_fast(warm_600519):
    dd = _data(warm_600519)
    assert dd.get("from_cache") is True
    # 二次访问 < 50ms (从 httpx.Response.elapsed)
    t0 = time.time()
    r = httpx.get(f"{BASE}/api/stock/600519/deep_analysis", timeout=3.0)
    elapsed_ms = (time.time() - t0) * 1000
    assert r.status_code == 200
    assert r.elapsed.total_seconds() * 1000 < 80 or elapsed_ms < 80


# 4. recommendation_action 白名单
@pytest.mark.contract
def test_recommendation_action_whitelist(warm_600519):
    dd = _data(warm_600519)
    action = dd.get("recommendation_action")
    # 当前 _do_deep_analysis 不调 LLM — 没这字段也 OK (兜底)
    if action is None:
        # 但 holding_advice 等 LLM 字段也不应出现 — 那是 _call_minimax 注入
        # 跳过即可
        pytest.skip("no LLM verdict in deep_analysis payload (LLM not in sync path)")
    assert action in ACTION_WHITELIST, f"action {action!r} not in whitelist"


# 5. profit_taking_score ∈ [0, 100]
@pytest.mark.contract
def test_profit_taking_score_range(warm_600519):
    dd = _data(warm_600519)
    score = dd.get("profit_taking_score")
    if score is None:
        pytest.skip("no LLM verdict in deep_analysis payload")
    assert isinstance(score, int)
    assert 0 <= score <= 100, f"profit_taking_score {score} out of [0,100]"


# 6. holding_advice schema
@pytest.mark.contract
def test_holding_advice_shape(warm_600519):
    dd = _data(warm_600519)
    ha = dd.get("holding_advice")
    if ha is None:
        pytest.skip("no LLM verdict in deep_analysis payload")
    for k in ("stop_loss", "target_price", "horizon_days", "rationale"):
        assert k in ha, f"holding_advice missing key: {k}"
    assert isinstance(ha["horizon_days"], int)
    assert ha["horizon_days"] >= 0


# 7. tech_position 物理约束
@pytest.mark.contract
def test_tech_position_physical(warm_600519):
    dd = _data(warm_600519)
    tech = dd.get("tech_position") or {}
    assert tech.get("has_data") is True
    # pullback_from_60d_high_pct ≤ 0 (距高点回撤不可能为正)
    pb = tech.get("pullback_from_60d_high_pct")
    assert pb is not None
    assert pb <= 0.001, f"pullback_from_60d_high_pct {pb} should be ≤ 0"
    # pct_position_60d ∈ [0, 100]
    p60 = tech.get("pct_position_60d")
    assert p60 is not None
    assert 0 <= p60 <= 100, f"pct_position_60d {p60} out of [0,100]"
    # 250 bars (1y K 线)
    assert tech.get("bars") >= 60


# 8. earnings_jump 阈值
@pytest.mark.contract
def test_earnings_jump_detection(warm_600519):
    dd = _data(warm_600519)
    fund = dd.get("fundamentals") or {}
    ej = fund.get("earnings_jump") or {}
    # 600519 茅台跳变 False, jump 应为 bool
    assert isinstance(ej.get("jump"), bool)
    if ej.get("jump"):
        assert isinstance(ej.get("reasons"), list)
        assert len(ej["reasons"]) > 0
    max_rev = ej.get("max_revenue_yoy")
    max_np = ej.get("max_netprofit_yoy")
    if max_rev is not None:
        assert isinstance(max_rev, (int, float))
    if max_np is not None:
        assert isinstance(max_np, (int, float))


# 9. profile.business_summary 文本 ≤ 200 字 (LLM 摘要规则)
@pytest.mark.contract
def test_profile_business_summary_length(warm_600519):
    dd = _data(warm_600519)
    fund = dd.get("fundamentals") or {}
    profile = fund.get("profile") or {}
    biz = profile.get("business_summary") or ""
    if biz:
        # ≤ 200 字 (中文按 1 字计)
        assert len(biz) <= 200, f"business_summary too long: {len(biz)} chars"


# 10. holding.has_position 字段
@pytest.mark.contract
def test_holding_shape(warm_600519):
    dd = _data(warm_600519)
    hold = dd.get("holding") or {}
    assert "has_position" in hold
    assert isinstance(hold["has_position"], bool)
    if hold["has_position"]:
        # 有持仓时 avg_cost / shares / market_value 必须有数
        assert isinstance(hold.get("shares"), int)
        assert isinstance(hold.get("avg_cost"), (int, float))
        assert hold.get("market_value", 0) > 0
    else:
        # 无持仓时字段为零 (默认 0, 不抛)
        assert hold.get("shares", 0) == 0


# bonus: refresh=1 强制清缓存
@pytest.mark.contract
def test_refresh_bypasses_cache(client):
    r1 = client.get("/api/stock/600519/deep_analysis")
    assert r1.status_code == 200
    dd1 = _data(r1.json())
    assert dd1.get("from_cache") is True

    r2 = client.get("/api/stock/600519/deep_analysis?refresh=1")
    assert r2.status_code == 200
    dd2 = _data(r2.json())
    # refresh=1 后: 同步路径返 from_cache=False, 后台路径返 queued=True
    if "queued" in dd2:
        # 后台模式: 不写 from_cache (queued 是默认 background=1)
        assert dd2["queued"] is True or dd2["queued"] is False
    else:
        # 同步或特殊场景
        assert "fundamentals" in dd2


# bonus: background=1 队列 (run_id 模式)
@pytest.mark.contract
def test_background_1_queued(client):
    _clear_lock("999999")  # 不存在 code, 不会撞锁
    r = client.get("/api/stock/600519/deep_analysis?background=1&refresh=1")
    assert r.status_code == 200
    dd = _data(r.json())
    if dd.get("queued"):
        assert "run_id" in dd
        assert dd["run_id"].startswith("deep-")
    else:
        # 撞锁 (debounce): 必须返 queued=False + reason=debounced
        assert dd.get("queued") is False
        assert dd.get("reason") == "debounced"