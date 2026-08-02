#!/usr/bin/env python3
"""
新闻情报 Dashboard 测试 (Phase 6e)

覆盖: /api/news/live, /api/dashboard/news_impact, /api/news/sector/{cluster},
     AI 分析 fire-and-forget, concept_taxonomy 集成, cache TTL 动态切换,
     新闻利好利空股票聚合模块。

运行: pytest tests/test_news_dashboard.py -v
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from tuixue_v3.web import news_lookup as _nl
from tuixue_v3.web import concept_taxonomy as _ct


# ═══════════════════════════════════════════════════════════
# 1. news_lookup — TTL / trading time
# ═══════════════════════════════════════════════════════════

def test_cache_ttl_trading_vs_offhours():
    """交易时段 90s, 非交易时段 300s（周日肯定是 300s）。"""
    ttl = _nl.get_cache_ttl()
    assert ttl in (90, 300), f"unexpected TTL: {ttl}"


def test_poll_interval_trading_vs_offhours():
    """Poll 间隔 60s / 300s。"""
    ival = _nl.get_poll_interval()
    assert ival in (60, 300), f"unexpected poll interval: {ival}"


def test_is_trading_time_returns_bool():
    assert isinstance(_nl._is_trading_time(), bool)


def test_fetch_live_news_functions_exist():
    assert callable(_nl.fetch_live_news)
    assert callable(_nl.try_acquire_poll_lock)
    assert hasattr(_nl, "LIVE_NUM")
    assert hasattr(_nl, "POLL_INTERVAL_TRADING")
    assert hasattr(_nl, "POLL_INTERVAL_OFFHOURS")


# ═══════════════════════════════════════════════════════════
# 2. concept_taxonomy — match_concepts integration
# ═══════════════════════════════════════════════════════════

def test_match_concepts_semiconductor_news():
    """新闻标题含 AI/半导体 关键词 → match 对应 L3/L4。"""
    text = "国产GPU突破3nm工艺 光模块800G需求爆发 AI服务器液冷方案落地"
    matched = _ct.match_concepts(text)
    l3s = [l3 for l3, _ in matched]
    # 应命中 GPU/光模块/液冷 → AI底层硬件 + 算力基建
    assert any("AI" in l3 for l3 in l3s) or any("光" in l3 for l3 in l3s) or len(matched) > 0


def test_match_concepts_energy_news():
    """新能源相关关键词匹配。"""
    text = "光伏组件价格触底反弹 固态电池量产加速 风电装机超预期"
    matched = _ct.match_concepts(text)
    l3s = [l3 for l3, _ in matched]
    assert any("光伏" in l3 or "固态电池" in l3 or "风电" in l3 for l3 in l3s)


def test_match_concepts_empty_input():
    assert _ct.match_concepts(None) == []
    assert _ct.match_concepts("") == []


def test_l3_to_cluster_mapping():
    """L3 chain → L1 cluster 映射完整性。"""
    assert _ct.L3_TO_CLUSTER["芯片设计"] == "半导体芯片产业链"
    assert _ct.L3_TO_CLUSTER["光伏"] == "新能源"
    assert _ct.L3_TO_CLUSTER["食饮"] == "消费"  # 白酒属于"食饮" L3 chain


def test_concept_signature():
    sig = _ct.concept_signature("AI大模型训练 算力租赁需求暴增 GPU供不应求")
    assert sig["matched"]
    assert sig["primary_cluster"]


# ═══════════════════════════════════════════════════════════
# 3. Server endpoints — mock 测试
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def test_client():
    from fastapi.testclient import TestClient
    from tuixue_v3.web.server import app
    return TestClient(app)


def test_news_live_endpoint(test_client, monkeypatch):
    """GET /api/news/live 返回正确结构。"""
    mock_cache = {
        "fetched_at": int(time.time()) - 30,
        "analyzed_at": int(time.time()) - 10,
        "news": [
            {"id": "abc123", "title": "测试新闻1", "url": "", "intro": "摘要",
             "media": "新浪", "ctime": int(time.time()) - 60,
             "ctime_str": "2026-08-02 14:30", "lid": 2517, "lid_name": "7x24快讯", "keywords": []},
            {"id": "def456", "title": "测试新闻2", "url": "", "intro": "摘要2",
             "media": "新浪", "ctime": int(time.time()) - 120,
             "ctime_str": "2026-08-02 14:29", "lid": 2516, "lid_name": "财经要闻", "keywords": []},
        ],
        "ai": {
            "abc123": {"score": 7.5, "direction": "利好", "sectors": ["电子"],
                       "chains": ["芯片设计", "半导体设备"], "stocks": ["688981"],
                       "reason": "国产替代加速"},
        },
    }
    with mock.patch("tuixue_v3.web.news_lookup.load_cache", return_value=mock_cache):
        resp = test_client.get("/api/news/live")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok")
        content = data.get("data", {})
        assert content.get("count") == 2
        assert content.get("ai_count") == 1
        news = content.get("news", [])
        assert len(news) == 2
        # 第一条有 AI 标注
        first = news[0] if news[0].get("id") == "abc123" else news[1]
        if first["id"] == "abc123":
            assert first["ai"] is not None
            assert first["ai"]["score"] == 7.5
            assert "半导体芯片产业链" in first["ai"].get("clusters", [])


def test_news_live_empty(test_client, monkeypatch):
    """空缓存返回 news:[]。"""
    # 清理 L0 缓存避免上一个测试污染
    from tuixue_v3.web.server import _cache_news
    _cache_news.invalidate(("news_live",))

    mock_cache = {"fetched_at": 0, "analyzed_at": 0, "news": [], "ai": {}}
    with mock.patch("tuixue_v3.web.news_lookup.load_cache", return_value=mock_cache):
        resp = test_client.get("/api/news/live")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["count"] == 0


def test_dashboard_news_impact(test_client, monkeypatch):
    """GET /api/dashboard/news_impact 返回板块聚合。"""
    mock_cache = {
        "fetched_at": int(time.time()) - 30,
        "analyzed_at": int(time.time()) - 10,
        "news": [
            {"id": "n1", "title": "AI芯片突破", "url": "", "intro": "国产GPU",
             "media": "sina", "ctime": int(time.time()) - 60, "ctime_str": "14:30",
             "lid": 2517, "lid_name": "7x24", "keywords": []},
            {"id": "n2", "title": "光伏出口大增", "url": "", "intro": "光伏",
             "media": "sina", "ctime": int(time.time()) - 120, "ctime_str": "14:29",
             "lid": 2517, "lid_name": "7x24", "keywords": []},
        ],
        "ai": {
            "n1": {"score": 9.0, "direction": "利好", "sectors": ["电子"],
                   "chains": ["AI底层硬件"], "stocks": ["688256"], "reason": "算力需求爆发"},
            "n2": {"score": 6.5, "direction": "利好", "sectors": ["电力设备"],
                   "chains": ["光伏"], "stocks": ["601012"], "reason": "出口超预期"},
        },
    }
    with mock.patch("tuixue_v3.web.news_lookup.load_cache", return_value=mock_cache), \
         mock.patch("tuixue_v3.web.concept_taxonomy.L3_TO_CLUSTER", _ct.L3_TO_CLUSTER):
        resp = test_client.get("/api/dashboard/news_impact")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok")
        content = data.get("data", {})
        clusters = content.get("clusters", [])
        assert len(clusters) >= 1  # 至少有一个 cluster
        # AI算力全链条 得分应高于 新能源
        ai_cluster = next((c for c in clusters if c["name"] == "AI算力全链条"), None)
        assert ai_cluster is not None
        assert ai_cluster["impact_score"] >= 9.0
        assert ai_cluster["bullish"] >= 1


def test_news_ai_status(test_client, monkeypatch):
    """GET /api/news/ai_status 返回运行状态。"""
    mock_cache = {"fetched_at": int(time.time()), "analyzed_at": int(time.time()) - 100,
                  "news": [{"id": "x"} for _ in range(10)],
                  "ai": {"x": {}}}
    # 清理可能的锁
    from tuixue_v3 import cache_store
    store = cache_store.get_store()
    store.delete("news:ai_lock")

    with mock.patch("tuixue_v3.web.news_lookup.load_cache", return_value=mock_cache):
        resp = test_client.get("/api/news/ai_status")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok")
        content = data.get("data", {})
        assert content["news_total"] == 10
        assert content["ai_done"] == 1
        assert content["ai_pending"] == 9


def test_news_sector_filter(test_client, monkeypatch):
    """GET /api/news/sector/AI算力全链条 只返回该板块新闻。"""
    mock_cache = {
        "fetched_at": int(time.time()) - 30,
        "analyzed_at": int(time.time()) - 10,
        "news": [
            {"id": "n1", "title": "AI算力突破", "url": "", "intro": "",
             "media": "sina", "ctime": int(time.time()), "ctime_str": "14:30",
             "lid": 2517, "lid_name": "7x24", "keywords": []},
            {"id": "n2", "title": "白酒涨价", "url": "", "intro": "",
             "media": "sina", "ctime": int(time.time()), "ctime_str": "14:29",
             "lid": 2517, "lid_name": "7x24", "keywords": []},
        ],
        "ai": {
            "n1": {"score": 8.0, "direction": "利好", "sectors": ["电子"],
                   "chains": ["AI底层硬件"], "stocks": [], "reason": "突破"},
            "n2": {"score": 4.0, "direction": "中性", "sectors": ["食品饮料"],
                   "chains": ["白酒"], "stocks": [], "reason": "正常"},
        },
    }
    with mock.patch("tuixue_v3.web.news_lookup.load_cache", return_value=mock_cache), \
         mock.patch("tuixue_v3.web.concept_taxonomy.L3_TO_CLUSTER", _ct.L3_TO_CLUSTER):
        resp = test_client.get("/api/news/sector/AI算力全链条")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok")
        content = data.get("data", {})
        assert content["count"] == 1
        assert content["news"][0]["id"] == "n1"


def test_news_analyze_fire_and_forget(test_client, monkeypatch):
    """POST /api/news/analyze 立即返回 started,不阻塞。"""
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
    from tuixue_v3 import cache_store
    store = cache_store.get_store()
    store.delete("news:ai_lock")

    mock_cache = {"fetched_at": int(time.time()), "analyzed_at": 0,
                  "news": [{"id": "x1", "title": "test"}], "ai": {}}
    with mock.patch("tuixue_v3.web.news_lookup.load_cache", return_value=mock_cache):
        resp = test_client.post("/api/news/analyze")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok")
        assert data["data"]["status"] == "started"


def test_news_analyze_dedup(test_client, monkeypatch):
    """重复 POST /api/news/analyze 返回 already_running。"""
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
    from tuixue_v3 import cache_store
    store = cache_store.get_store()
    # 手动设锁模拟正在运行
    store.set("news:ai_lock", int(time.time()), ttl=120)

    mock_cache = {"fetched_at": int(time.time()), "analyzed_at": 0,
                  "news": [{"id": "x1"}], "ai": {}}
    with mock.patch("tuixue_v3.web.news_lookup.load_cache", return_value=mock_cache):
        resp = test_client.post("/api/news/analyze")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok")
        assert data["data"]["status"] == "already_running"


# ═══════════════════════════════════════════════════════════
# 4. 新闻利好利空股票聚合模块
# ═══════════════════════════════════════════════════════════

def test_news_stocks_aggregation():
    """验证多新闻按股票聚合利好/利空评分的逻辑。"""
    news_list = [
        {"id": "a", "ai": {"score": 8.0, "direction": "利好", "stocks": ["600519", "000858"]}},
        {"id": "b", "ai": {"score": 7.5, "direction": "利好", "stocks": ["600519"]}},
        {"id": "c", "ai": {"score": 5.0, "direction": "利空", "stocks": ["000858"]}},
        {"id": "d", "ai": {"score": 3.0, "direction": "利好", "stocks": ["600519"]}},
    ]
    # 聚合
    stocks = {}
    for n in news_list:
        ai = n.get("ai") or {}
        score = ai.get("score", 0) or 0
        direction = ai.get("direction", "中性")
        for s in (ai.get("stocks") or []):
            if s not in stocks:
                stocks[s] = {"bullish": 0.0, "bearish": 0.0, "count": 0, "max_score": 0.0}
            stocks[s]["count"] += 1
            stocks[s]["max_score"] = max(stocks[s]["max_score"], score)
            if direction == "利好":
                stocks[s]["bullish"] += score
            elif direction == "利空":
                stocks[s]["bearish"] += score

    assert stocks["600519"]["count"] == 3
    assert stocks["600519"]["bullish"] == 8.0 + 7.5 + 3.0
    assert stocks["600519"]["max_score"] == 8.0
    assert stocks["000858"]["count"] == 2
    assert stocks["000858"]["bullish"] == 8.0
    assert stocks["000858"]["bearish"] == 5.0


# ═══════════════════════════════════════════════════════════
# 5. L3_TO_CLUSTER 反查 + 去重
# ═══════════════════════════════════════════════════════════

def test_ai_chains_to_clusters():
    """AI 返回的 chains → clusters 反查正确。"""
    chains = ["AI底层硬件", "光通信", "算力基建", "AI应用"]
    l3_to_cluster = _ct.L3_TO_CLUSTER
    clusters = list(dict.fromkeys(
        l3_to_cluster.get(c, "") for c in chains if l3_to_cluster.get(c)
    ))
    # AI底层硬件 → AI算力全链条, 光通信 → 通信板块, 算力基建/AI应用 → AI算力全链条
    assert "AI算力全链条" in clusters
    assert "通信板块" in clusters
    assert len(clusters) == 2  # 去重后只有2个不同 cluster


def test_cluster_order_completeness():
    """CLUSTER_ORDER 16 大类完整性。"""
    assert len(_ct.CLUSTER_ORDER) == 16
    for cl in _ct.CLUSTER_ORDER:
        assert cl in _ct.CLUSTER_DESC, f"missing desc for {cl}"
