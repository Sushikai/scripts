"""/api/today 端点测试。"""

import json
import urllib.request


def test_today_shape(client):
    """GET /api/today 返回 24 小时桶 + 计数。"""
    r = client.get("/api/today")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    data = body["data"]
    for k in ("hours", "total_jobs", "total_uploads", "cron_running", "peak_hour", "ts"):
        assert k in data
    assert len(data["hours"]) == 24
    assert data["total_jobs"] == sum(h["jobs"] for h in data["hours"])
    assert data["total_uploads"] == sum(h["uploads"] for h in data["hours"])


def test_today_hour_shape(client):
    """每个 hour 字段完整。"""
    body = client.get("/api/today").json()
    for h in body["data"]["hours"]:
        for k in ("hour", "label", "jobs", "uploads", "samples"):
            assert k in h
        assert 0 <= h["hour"] <= 23
        assert h["label"] == f"{h['hour']:02d}:00"
        assert isinstance(h["samples"], list)


def test_today_via_server(flow_server):
    """HTTP 端到端。"""
    body = json.loads(urllib.request.urlopen(flow_server["base"] + "/api/today").read())
    assert body["ok"] is True
    assert len(body["data"]["hours"]) == 24


def test_today_peak_hour_matches(client):
    """peak_hour 是 jobs+uploads 最多的那小时。"""
    body = client.get("/api/today").json()
    d = body["data"]
    if d["peak_hour"]:
        peak = max(d["hours"], key=lambda h: h["jobs"] + h["uploads"])
        assert d["peak_hour"] == peak["label"]


def test_today_sample_shape(client):
    """samples 项字段。"""
    body = client.get("/api/today").json()
    for h in body["data"]["hours"]:
        for s in h["samples"]:
            for k in ("name", "tool", "step"):
                assert k in s