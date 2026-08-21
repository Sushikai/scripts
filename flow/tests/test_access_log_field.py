"""/api/log/recent 端点契约测试:确认字段名是 latency_ms,防止前端误用 duration_ms。"""

import json
import urllib.request


def test_log_recent_returns_latency_ms_field(client):
    """/api/log/recent 返回的 log entry 必须有 latency_ms 字段(不是 duration_ms)"""
    r = client.get("/api/log/recent?limit=10")
    body = r.json()
    assert body["ok"] is True
    lines = body["data"]["lines"]
    assert isinstance(lines, list)
    if not lines:
        return  # 没数据不验证
    # 每行是 JSON 字符串,需先 parse
    first = json.loads(lines[0])
    assert "latency_ms" in first, f"字段名应是 latency_ms,实际: {list(first.keys())}"
    assert "method" in first
    assert "path" in first
    assert "status" in first
    assert isinstance(first["latency_ms"], (int, float))


def test_log_recent_via_server(flow_server):
    """HTTP 端到端,字段名契约。"""
    body = json.loads(
        urllib.request.urlopen(flow_server["base"] + "/api/log/recent?limit=5").read()
    )
    assert body["ok"] is True
    lines = body["data"]["lines"]
    if not lines:
        return
    entry = json.loads(lines[0])
    # 字段名必须一致:前端读 latency_ms,后端必须返回 latency_ms
    assert "latency_ms" in entry
    assert "duration_ms" not in entry or entry.get("duration_ms") is None


def test_log_recent_no_undefined_fields():
    """契约:每个 entry 必须有 method/path/status,不能是 undefined/None"""
    # 直接 HTTP(不依赖 client fixture)
    body = json.loads(
        urllib.request.urlopen("http://localhost:8810/api/log/recent?limit=5").read()
    )
    lines = body["data"]["lines"]
    for ln in lines:
        entry = json.loads(ln)
        # method/path/status 必须是字符串
        assert isinstance(entry.get("method"), str), f"method 应是 str,实为 {type(entry.get('method'))}"
        assert isinstance(entry.get("path"), str)
        assert isinstance(entry.get("status"), int)