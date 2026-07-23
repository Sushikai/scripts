"""DB repo 单元测试(不依赖 server,直接调函数)。"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _fresh_db():
    """每个测试用临时 DB。"""
    import os
    tmp = Path(tempfile.mkdtemp(prefix="flow_repo_"))
    os.environ["FLOW_DB"] = str(tmp / "flow.db")
    # 重置 module-level init 标记
    import importlib
    import backend.db.repo as r
    importlib.reload(r)
    return r


def test_project_create_and_get():
    r = _fresh_db()
    p = r.project_create("info_gap", "测试项目", {"topic": "AI"}, {"k": 1})
    assert p["id"]
    assert p["tool_id"] == "info_gap"
    assert p["status"] == "pending"
    fetched = r.project_get(p["id"])
    assert fetched["name"] == "测试项目"
    assert fetched["params"]["topic"] == "AI"


def test_project_update_status():
    r = _fresh_db()
    p = r.project_create("fengge", "x", {})
    r.project_update_status(p["id"], "running")
    assert r.project_get(p["id"])["status"] == "running"


def test_project_list_filter():
    r = _fresh_db()
    r.project_create("info_gap", "a", {})
    r.project_create("fengge", "b", {})
    r.project_create("info_gap", "c", {})
    only_ig = r.project_list(tool_id="info_gap")
    assert len(only_ig) == 2
    assert all(p["tool_id"] == "info_gap" for p in only_ig)


def test_job_lifecycle():
    r = _fresh_db()
    p = r.project_create("info_gap", "j", {})
    j = r.job_create(p["id"], "research")
    assert j["status"] == "pending"
    r.job_set_running(j["id"])
    assert r.job_get(j["id"])["status"] == "running"
    r.job_set_progress(j["id"], 0.42)
    assert abs(r.job_get(j["id"])["progress"] - 0.42) < 0.01
    r.job_set_done(j["id"], {"output": "script.json"})
    final = r.job_get(j["id"])
    assert final["status"] == "done"
    assert final["progress"] == 1.0
    assert final["artifacts"]["output"] == "script.json"


def test_job_failed():
    r = _fresh_db()
    p = r.project_create("fengge", "x", {})
    j = r.job_create(p["id"], "download")
    r.job_set_failed(j["id"], "B站 cookies 过期")
    final = r.job_get(j["id"])
    assert final["status"] == "failed"
    assert "cookies" in final["error"]


def test_job_cancelled_only_pending_running():
    r = _fresh_db()
    p = r.project_create("info_gap", "x", {})
    j1 = r.job_create(p["id"], "voice")
    r.job_set_done(j1["id"], {})
    r.job_set_cancelled(j1["id"])  # 不应改 done
    assert r.job_get(j1["id"])["status"] == "done"
    j2 = r.job_create(p["id"], "compose")
    r.job_set_running(j2["id"])
    r.job_set_cancelled(j2["id"])
    assert r.job_get(j2["id"])["status"] == "cancelled"


def test_asset_create_and_list():
    r = _fresh_db()
    a = r.asset_create("douyin", "https://v.douyin.com/abc", "/tmp/x.mp4", "deadbeef", ["萌娃", "搞笑"])
    assert a["source"] == "douyin"
    listed = r.asset_list(source="douyin")
    assert any(x["hash"] == "deadbeef" for x in listed)


def test_upload_lifecycle():
    r = _fresh_db()
    p = r.project_create("info_gap", "x", {})
    u = r.upload_create(p["id"], "bilibili", "kaikai_main")
    r.upload_set_success(u["id"], "BV1xxxx")
    assert u["status"] == "pending"
    listed = r.upload_list(platform="bilibili")
    assert listed[0]["vid_id"] == "BV1xxxx"


def test_account_upsert():
    r = _fresh_db()
    r.account_upsert("kaikai_main", "bilibili", "/path/cookies.txt", "ok")
    accounts = r.account_list()
    assert len(accounts) == 1
    assert accounts[0]["name"] == "kaikai_main"
    r.account_upsert("kaikai_main", "bilibili", "/path/cookies.txt", "fail")
    # OR REPLACE 路径下应仍是 1 条(用 name+platform 做唯一)
    accounts2 = r.account_list()
    assert len(accounts2) == 1