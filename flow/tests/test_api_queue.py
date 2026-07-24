"""/api/queue 端点测试。"""

import json
import urllib.request


def test_queue_shape(client):
    """GET /api/queue 返回必要字段。"""
    r = client.get("/api/queue")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    data = body["data"]
    for k in ("inflight", "inflight_count", "pending", "pending_count", "max_concurrent", "utilization", "queue_depth", "ts"):
        assert k in data
    assert isinstance(data["max_concurrent"], int)
    assert data["max_concurrent"] >= 1


def test_queue_inflight_item_shape(client):
    """inflight 项字段。"""
    body = client.get("/api/queue").json()
    items = body["data"]["inflight"]
    if items:
        first = items[0]
        for k in ("job_id", "project_id", "project_name", "step", "progress", "status", "started_at"):
            assert k in first
        assert 0 <= first["progress"] <= 1


def test_queue_via_server(flow_server):
    """HTTP 端到端 + utilization 计算。"""
    body = json.loads(urllib.request.urlopen(flow_server["base"] + "/api/queue").read())
    assert body["ok"] is True
    d = body["data"]
    assert 0 <= d["utilization"] <= 1.0
    assert d["queue_depth"] >= 0


def test_queue_consistent_counts(client):
    """inflight_count == len(inflight) + pending_count == len(pending)。"""
    body = client.get("/api/queue").json()
    d = body["data"]
    assert d["inflight_count"] == len(d["inflight"])
    assert d["pending_count"] == len(d["pending"])