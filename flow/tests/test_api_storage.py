"""/api/storage 端点测试。"""

import json
import urllib.request


def test_storage_shape(client):
    """GET /api/storage 返回 items + total + ts。"""
    r = client.get("/api/storage")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    data = body["data"]
    for k in ("items", "total_bytes", "total_human", "count", "ts"):
        assert k in data
    assert len(data["items"]) >= 5
    assert data["total_bytes"] == sum(x["size_bytes"] for x in data["items"])
    assert data["count"] == len(data["items"])


def test_storage_item_shape(client):
    """每个 item 字段。"""
    body = client.get("/api/storage").json()
    for it in body["data"]["items"]:
        for k in ("name", "path", "exists", "size_bytes", "size_human"):
            assert k in it


def test_storage_sorted_desc(client):
    """按 size 倒序。"""
    body = client.get("/api/storage").json()
    sizes = [it["size_bytes"] for it in body["data"]["items"]]
    assert sizes == sorted(sizes, reverse=True)


def test_storage_disk(client):
    """GET /api/storage/disk 返回根分区。"""
    body = client.get("/api/storage/disk").json()
    assert body["ok"] is True
    data = body["data"]
    if "error" not in data:
        for k in ("total_bytes", "used_bytes", "avail_bytes", "used_human", "avail_human", "pct"):
            assert k in data
        assert 0 <= data["pct"] <= 100


def test_storage_top_files(client):
    """存在且是目录的 item 有 top_files 数组。"""
    body = client.get("/api/storage").json()
    for it in body["data"]["items"]:
        if it["exists"] and not it.get("is_file"):
            assert "top_files" in it
            if it["top_files"]:
                first = it["top_files"][0]
                assert "path" in first
                assert "size_human" in first


def test_storage_via_server(flow_server):
    """HTTP 端到端。"""
    body = json.loads(urllib.request.urlopen(flow_server["base"] + "/api/storage").read())
    assert body["ok"] is True
    assert body["data"]["count"] >= 5