"""/api/crons/{label}/log 端点测试。"""

import json
import urllib.request


def test_cron_log_404(client):
    """不存在的 cron → 404。"""
    r = client.get("/api/crons/nonexistent.cron.label/log")
    assert r.status_code == 404


def test_cron_log_shape(client):
    """已知 cron 返必要字段。"""
    body = client.get("/api/crons/com.kaikai.bilibili-dm/log?lines=10").json()
    assert body["ok"] is True
    data = body["data"]
    for k in ("label", "schedule", "stdout_path", "stderr_path", "stdout_lines", "stderr_lines", "stdout_size", "stderr_size"):
        assert k in data
    assert isinstance(data["stdout_lines"], list)


def test_cron_log_lines_shape(client):
    """stdout_lines 项字段。"""
    body = client.get("/api/crons/com.kaikai.bilibili-dm/log?lines=5").json()
    lines = body["data"]["stdout_lines"]
    if lines:
        first = lines[0]
        for k in ("n", "line"):
            assert k in first


def test_cron_log_lines_param(client):
    """?lines=N 控制返回行数。"""
    body = client.get("/api/crons/com.kaikai.bilibili-dm/log?lines=3").json()
    assert len(body["data"]["stdout_lines"]) <= 3
    assert len(body["data"]["stderr_lines"]) <= 3


def test_cron_log_via_server(flow_server):
    """HTTP 端到端。"""
    body = json.loads(urllib.request.urlopen(flow_server["base"] + "/api/crons/com.kaikai.bilibili-dm/log?lines=5").read())
    assert body["ok"] is True
    assert body["data"]["label"] == "com.kaikai.bilibili-dm"