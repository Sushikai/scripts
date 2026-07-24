"""/api/crons 端点测试。"""

import json
import urllib.request


def test_crons_shape(client):
    """GET /api/crons 返回必要字段。"""
    r = client.get("/api/crons")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    data = body["data"]
    assert "items" in data
    assert "total_loaded" in data
    # 字段
    if data["items"]:
        first = data["items"][0]
        for k in ("label", "schedule", "running", "pid", "last_status", "stdout_path", "stderr_path"):
            assert k in first


def test_crons_summary(client):
    """GET /api/crons/summary 返回 KPI。"""
    body = client.get("/api/crons/summary").json()
    assert body["ok"] is True
    for k in ("total", "running", "stopped", "failed_exit", "with_logs"):
        assert k in body["data"]
    assert body["data"]["total"] == body["data"]["running"] + body["data"]["stopped"]


def test_crons_via_server(flow_server):
    """HTTP 端到端 + 字段全。"""
    body = json.loads(urllib.request.urlopen(flow_server["base"] + "/api/crons").read())
    assert body["ok"] is True
    items = body["data"]["items"]
    # 至少有 1 个 cron 任务(launchd 是系统基础)
    if items:
        for it in items:
            assert it["label"].startswith(("com.kaikai.", "com.scripts.", "com.bilibili.fan-", "ai.openclaw."))
            assert isinstance(it["running"], bool)
            assert isinstance(it["pid"], str)
            assert isinstance(it["last_status"], str)


def test_crons_summary_consistent(flow_server):
    """/api/crons/summary 数据自洽。"""
    body = json.loads(urllib.request.urlopen(flow_server["base"] + "/api/crons/summary").read())
    assert body["ok"] is True
    d = body["data"]
    assert d["total"] >= 0
    assert d["running"] >= 0
    assert d["running"] <= d["total"]