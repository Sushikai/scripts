"""material_collector 端到端 dry-run:4 步全跑通。"""

import time


def test_material_full_pipeline_dry_run(client):
    """建项目 → 4 步依次提交 → 等所有 done → 查产物。"""
    p = client.post("/api/projects", json={
        "tool_id": "material_collector",
        "name": "mat_e2e",
        "params": {"dry_run": True},
    }).json()["data"]
    pid = p["id"]

    job_ids = []
    for step in ["web_collect", "adb_collect", "process", "export_assets"]:
        r = client.post("/api/jobs", json={
            "tool_id": "material_collector",
            "project_id": pid,
            "step": step,
            "params": {"platforms": ["douyin"], "keyword": "热门"},
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


def test_material_single_step(client):
    """单独跑 web_collect 步。"""
    p = client.post("/api/projects", json={
        "tool_id": "material_collector", "name": "single_step", "params": {},
    }).json()["data"]
    j = client.post("/api/jobs", json={
        "tool_id": "material_collector",
        "project_id": p["id"],
        "step": "web_collect",
        "params": {"platforms": ["bilibili"]},
    }).json()["data"]
    for _ in range(30):
        time.sleep(0.2)
        g = client.get(f"/api/job/{j['job_id']}").json()["data"]
        if g["status"] in ("done", "failed"):
            break
    assert g["status"] == "done"


def test_material_cancel(client):
    """跑 web_collect 步然后立刻 cancel。"""
    p = client.post("/api/projects", json={
        "tool_id": "material_collector", "name": "cancel_test", "params": {},
    }).json()["data"]
    j = client.post("/api/jobs", json={
        "tool_id": "material_collector",
        "project_id": p["id"],
        "step": "web_collect",
        "params": {},
    }).json()["data"]
    r = client.post(f"/api/job/{j['job_id']}/cancel")
    assert r.status_code == 200
    time.sleep(1.0)
    g = client.get(f"/api/job/{j['job_id']}").json()["data"]
    assert g["status"] in ("cancelled", "done", "failed")


def test_material_tools_metadata(client):
    """GET /api/tools 应包含 material_collector 4 步。"""
    r = client.get("/api/tools")
    assert r.status_code == 200
    body = r.json()
    tools = {t["tool_id"]: t for t in body["data"]["tools"]}
    assert "material_collector" in tools
    mat = tools["material_collector"]
    assert len(mat["steps"]) == 4
    assert "web_collect" in mat["steps"]


def test_all_four_tools_listed(client):
    """4 个 wrapper 全部注册。"""
    r = client.get("/api/tools")
    body = r.json()
    tools = {t["tool_id"] for t in body["data"]["tools"]}
    assert {"info_gap", "fengge", "tiktok_story", "material_collector"}.issubset(tools)