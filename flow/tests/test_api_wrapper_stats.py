"""/api/wrapper-stats 端点测试。"""

import json
import urllib.request


def test_list_wrapper_stats(client):
    """GET /api/wrapper-stats 返回按 tool_id 聚合统计。"""
    r = client.get("/api/wrapper-stats")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "items" in body["data"]
    for s in body["data"]["items"]:
        assert "tool_id" in s
        assert "total" in s
        assert "success_rate" in s
        assert "avg_duration_ms" in s


def test_wrapper_stats_via_server(flow_server):
    """HTTP 端到端。"""
    body = json.loads(urllib.request.urlopen(flow_server["base"] + "/api/wrapper-stats").read())
    assert body["ok"] is True
    # 当前测试环境至少有 1 个 wrapper 运行过
    if body["data"]["count"] > 0:
        first = body["data"]["items"][0]
        assert first["total"] >= 1


def test_wrapper_stats_days_param(flow_server):
    """?days=7 限制范围。"""
    body = json.loads(urllib.request.urlopen(flow_server["base"] + "/api/wrapper-stats?days=7").read())
    assert body["ok"] is True
    assert body["data"]["days"] == 7


def test_wrapper_stats_detail_unknown(flow_server):
    """不存在的 tool_id 返 200 + 空数据。"""
    body = json.loads(urllib.request.urlopen(flow_server["base"] + "/api/wrapper-stats/nonexistent_tool").read())
    assert body["ok"] is True
    assert body["data"]["total"] == 0
    assert body["data"]["recent_jobs"] == []