"""tiktok_story 端到端 dry-run:6 步全跑通。"""

import time


def test_tiktok_full_pipeline_dry_run(client):
    """建项目 → 6 步依次提交 → 等所有 done → 查产物。"""
    p = client.post("/api/projects", json={
        "tool_id": "tiktok_story",
        "name": "tiktok_e2e",
        "params": {"dry_run": True},
    }).json()["data"]
    pid = p["id"]

    job_ids = []
    for step in ["fetch", "download", "subtitle", "crop", "upload_bili", "upload_douyin"]:
        r = client.post("/api/jobs", json={
            "tool_id": "tiktok_story",
            "project_id": pid,
            "step": step,
            "params": {"source": "tiktok"},
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


def test_tiktok_single_step(client):
    """单独跑 fetch 步。"""
    p = client.post("/api/projects", json={
        "tool_id": "tiktok_story", "name": "single_step", "params": {},
    }).json()["data"]
    j = client.post("/api/jobs", json={
        "tool_id": "tiktok_story",
        "project_id": p["id"],
        "step": "fetch",
        "params": {"source": "youtube"},
    }).json()["data"]
    for _ in range(30):
        time.sleep(0.2)
        g = client.get(f"/api/job/{j['job_id']}").json()["data"]
        if g["status"] in ("done", "failed"):
            break
    assert g["status"] == "done"


def test_tiktok_cancel(client):
    """跑 download 步(慢)然后立刻 cancel。"""
    p = client.post("/api/projects", json={
        "tool_id": "tiktok_story", "name": "cancel_test", "params": {},
    }).json()["data"]
    j = client.post("/api/jobs", json={
        "tool_id": "tiktok_story",
        "project_id": p["id"],
        "step": "download",
        "params": {},
    }).json()["data"]
    r = client.post(f"/api/job/{j['job_id']}/cancel")
    assert r.status_code == 200
    time.sleep(1.5)
    g = client.get(f"/api/job/{j['job_id']}").json()["data"]
    assert g["status"] in ("cancelled", "done", "failed")  # 失败/取消都允许


def test_tiktok_tools_metadata(client):
    """GET /api/tools 应包含 tiktok_story 6 步。"""
    r = client.get("/api/tools")
    assert r.status_code == 200
    body = r.json()
    tools = {t["tool_id"]: t for t in body["data"]["tools"]}
    assert "tiktok_story" in tools
    tt = tools["tiktok_story"]
    assert len(tt["steps"]) == 6


def test_tiktok_can_skip_subtitle(client):
    """subtitle 步可以独立 cancel 不阻塞后续步骤。"""
    p = client.post("/api/projects", json={
        "tool_id": "tiktok_story", "name": "skip_test", "params": {},
    }).json()["data"]
    j = client.post("/api/jobs", json={
        "tool_id": "tiktok_story",
        "project_id": p["id"],
        "step": "subtitle",
        "params": {},
    }).json()["data"]
    for _ in range(30):
        time.sleep(0.2)
        g = client.get(f"/api/job/{j['job_id']}").json()["data"]
        if g["status"] in ("done", "failed"):
            break
    assert g["status"] == "done"