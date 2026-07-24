"""/api/scripts 端点测试。"""

import json
import urllib.request


def test_list_scripts(client):
    """GET /api/scripts 返回已知脚本清单。"""
    r = client.get("/api/scripts")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    items = body["data"]["items"]
    assert len(items) >= 10
    # 每条都有 category / exists / kind / size_human
    for s in items:
        assert "category" in s
        assert "exists" in s
        assert "kind" in s
        assert "size_human" in s


def test_list_scripts_via_server(flow_server):
    """HTTP 端到端验证。"""
    body = json.loads(urllib.request.urlopen(flow_server["base"] + "/api/scripts").read())
    assert body["ok"] is True
    # 至少包含 fengge_pipeline
    names = [s["name"] for s in body["data"]["items"]]
    assert "fengge_pipeline" in names
    assert "fan_hunter" in names


def test_scripts_have_categories(flow_server):
    """分类齐全:wrapper/comment/upload/voice/lib。"""
    body = json.loads(urllib.request.urlopen(flow_server["base"] + "/api/scripts").read())
    cats = {s["category"] for s in body["data"]["items"]}
    assert "wrapper" in cats
    assert "comment" in cats


def test_scripts_by_category(flow_server):
    """GET /api/scripts/category/wrapper 只返 wrapper。"""
    body = json.loads(urllib.request.urlopen(flow_server["base"] + "/api/scripts/category/wrapper").read())
    assert body["ok"] is True
    for s in body["data"]["items"]:
        assert s["category"] == "wrapper"


def test_human_size():
    from backend.routers.scripts import _human_size
    assert _human_size(0) == "0 B"
    assert _human_size(1023) == "1023 B"
    assert _human_size(1024) == "1.0 KB"
    assert _human_size(1024 * 1024) == "1.00 MB"


def test_check_missing(tmp_path):
    """缺失文件返回 exists=False + kind=missing + size_human='—'。"""
    from backend.routers.scripts import _check
    info = _check(str(tmp_path / "missing.py"))
    assert info["exists"] is False
    assert info["kind"] == "missing"
    assert info["size_human"] == "—"


def test_check_file(tmp_path):
    """存在的文件返回 exists=True + size + mtime。"""
    from backend.routers.scripts import _check
    p = tmp_path / "x.py"
    p.write_text("hello")
    info = _check(str(p))
    assert info["exists"] is True
    assert info["kind"] == "file"
    assert info["size_bytes"] == 5
    assert info["mtime"] is not None


def test_check_dir(tmp_path):
    """目录返回 exists=True + kind=dir。"""
    from backend.routers.scripts import _check
    info = _check(str(tmp_path))
    assert info["exists"] is True
    assert info["kind"] == "dir"