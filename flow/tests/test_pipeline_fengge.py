"""fengge 端到端 dry-run:5 步全跑通。"""

import os
import time


def test_fengge_full_pipeline_dry_run(client):
    """建项目 → 5 步依次提交 → 等所有 done → 查产物。"""
    p = client.post("/api/projects", json={
        "tool_id": "fengge",
        "name": "fengge_e2e",
        "params": {"dry_run": True},
    }).json()["data"]
    pid = p["id"]

    job_ids = []
    for step in ["fetch_candidates", "download", "crop", "generate_meta", "upload"]:
        r = client.post("/api/jobs", json={
            "tool_id": "fengge",
            "project_id": pid,
            "step": step,
            "params": {},
        })
        assert r.status_code == 202, f"{step} got {r.status_code}: {r.text}"
        job_ids.append(r.json()["data"]["job_id"])

    deadline = time.time() + 30
    while time.time() < deadline:
        all_done = True
        for jid in job_ids:
            j = client.get(f"/api/job/{jid}").json()["data"]
            if j["status"] != "done":
                all_done = False
                break
        if all_done:
            break
        time.sleep(0.5)

    for jid in job_ids:
        j = client.get(f"/api/job/{jid}").json()["data"]
        assert j["status"] == "done", f"job {jid} status={j['status']}"
        assert j["progress"] == 1.0
        assert "output" in j["artifacts"]


def test_fengge_single_step(client):
    """单独跑 fetch_candidates 步。"""
    p = client.post("/api/projects", json={
        "tool_id": "fengge", "name": "single_step", "params": {},
    }).json()["data"]
    j = client.post("/api/jobs", json={
        "tool_id": "fengge",
        "project_id": p["id"],
        "step": "fetch_candidates",
        "params": {},
    }).json()["data"]
    for _ in range(30):
        time.sleep(0.2)
        g = client.get(f"/api/job/{j['job_id']}").json()["data"]
        if g["status"] in ("done", "failed"):
            break
    assert g["status"] == "done"


def test_fengge_cancel(client):
    """跑 fetch_candidates(慢)然后立刻 cancel。"""
    p = client.post("/api/projects", json={
        "tool_id": "fengge", "name": "cancel_test", "params": {},
    }).json()["data"]
    j = client.post("/api/jobs", json={
        "tool_id": "fengge",
        "project_id": p["id"],
        "step": "fetch_candidates",
        "params": {},
    }).json()["data"]
    r = client.post(f"/api/job/{j['job_id']}/cancel")
    assert r.status_code == 200
    time.sleep(1.0)
    g = client.get(f"/api/job/{j['job_id']}").json()["data"]
    # 状态要么 cancelled 要么 done(如果太快跑完)
    assert g["status"] in ("cancelled", "done")


def test_fengge_tools_metadata(client):
    """GET /api/tools 应包含 fengge 元数据 + 5 步。"""
    r = client.get("/api/tools")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    tools = {t["tool_id"]: t for t in body["data"]["tools"]}
    assert "fengge" in tools
    fg = tools["fengge"]
    assert len(fg["steps"]) == 5
    assert "fetch_candidates" in fg["steps"]


def test_fengge_tool_detail(client):
    """GET /api/tools/fengge 返回详细元数据。"""
    r = client.get("/api/tools/fengge")
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["tool_id"] == "fengge"
    assert body["data"]["name"]


def test_fengge_unknown_tool_404(client):
    r = client.get("/api/tools/no_such_tool")
    assert r.status_code == 404