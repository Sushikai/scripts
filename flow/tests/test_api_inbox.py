"""/api/inbox 端点测试。"""

import json
import urllib.request


def test_inbox_shape(client):
    """GET /api/inbox 返回 items + counts。"""
    r = client.get("/api/inbox")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    data = body["data"]
    for k in ("items", "count", "error_count", "warn_count", "ts"):
        assert k in data
    assert data["count"] == len(data["items"])
    assert data["count"] == data["error_count"] + data["warn_count"]


def test_inbox_item_shape(client):
    """每个 item 有 severity/category/title/detail。"""
    body = client.get("/api/inbox").json()
    if body["data"]["items"]:
        first = body["data"]["items"][0]
        for k in ("severity", "category", "title", "detail"):
            assert k in first
        assert first["severity"] in ("error", "warn")
        assert first["category"] in ("job", "cookie", "cron", "disk")


def test_inbox_via_server(flow_server):
    """HTTP 端到端。"""
    body = json.loads(urllib.request.urlopen(flow_server["base"] + "/api/inbox").read())
    assert body["ok"] is True
    d = body["data"]
    # 当前测试 DB 没数据时 count 仍 >= 0(可能只有 cron/disk alerts)
    assert d["count"] >= 0
    # 如果有 cron 告警或 disk 告警则 sort 后 error 在前
    if d["items"]:
        first_sev = d["items"][0]["severity"]
        assert first_sev == "error"  # sort 后第一一定是 error


def test_inbox_counts_match(client):
    """error_count + warn_count == count,且分类求和 == sum。"""
    body = client.get("/api/inbox").json()
    d = body["data"]
    assert d["error_count"] + d["warn_count"] == d["count"]
    # 分类计数
    cats: dict[str, int] = {}
    for it in d["items"]:
        cats[it["category"]] = cats.get(it["category"], 0) + 1
    # 不超每类上限 (10)
    for cat, n in cats.items():
        assert n <= 10, f"{cat} {n} > 10"