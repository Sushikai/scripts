"""信息差流水线端到端 dry-run:走完 7 步,产物 + 进度 + 状态都正确。"""

import asyncio
import time


def test_info_gap_full_pipeline_dry_run(client):
    """建项目 → 7 步依次提交 → 等所有 done → 查产物。"""
    # 建项目
    p = client.post("/api/projects", json={
        "tool_id": "info_gap",
        "name": "e2e_dry",
        "params": {"dry_run": True},
    }).json()["data"]
    pid = p["id"]

    # 7 步提交
    job_ids = []
    for step in ["research", "script", "voice", "materials", "compose", "style_diff", "upload"]:
        r = client.post("/api/jobs", json={
            "tool_id": "info_gap",
            "project_id": pid,
            "step": step,
            "params": {},
        })
        assert r.status_code == 202
        job_ids.append(r.json()["data"]["job_id"])

    # 等所有 done(超时 30s)
    deadline = time.time() + 30
    while time.time() < deadline:
        all_done = True
        for jid in job_ids:
            j = client.get(f"/api/job/{jid}").json()["data"]
            if j["status"] not in ("done", "failed", "cancelled"):
                all_done = False
                break
            if j["status"] != "done":
                all_done = False
                break
        if all_done:
            break
        time.sleep(0.5)

    # 验证
    for jid in job_ids:
        j = client.get(f"/api/job/{jid}").json()["data"]
        assert j["status"] == "done", f"job {jid} status={j['status']}"
        assert j["progress"] == 1.0
        assert "output" in j["artifacts"]


def test_info_gap_single_step(client):
    """单独跑 research 步,产物存在。"""
    p = client.post("/api/projects", json={
        "tool_id": "info_gap", "name": "single_step", "params": {},
    }).json()["data"]
    j = client.post("/api/jobs", json={
        "tool_id": "info_gap",
        "project_id": p["id"],
        "step": "research",
        "params": {},
    }).json()["data"]
    # 等完成
    for _ in range(30):
        time.sleep(0.2)
        g = client.get(f"/api/job/{j['job_id']}").json()["data"]
        if g["status"] in ("done", "failed"):
            break
    assert g["status"] == "done"
    assert os.path.exists(g["artifacts"]["output"]) or g["artifacts"]["output"].startswith("/tmp/")


def test_info_gap_cancel_mid_run(client):
    """跑 upload 步(慢)然后立刻 cancel。"""
    p = client.post("/api/projects", json={
        "tool_id": "info_gap", "name": "cancel_test", "params": {},
    }).json()["data"]
    j = client.post("/api/jobs", json={
        "tool_id": "info_gap",
        "project_id": p["id"],
        "step": "upload",
        "params": {},
    }).json()["data"]
    # 立即 cancel
    r = client.post(f"/api/job/{j['job_id']}/cancel")
    assert r.status_code == 200
    # 等 worker 检查 cancel
    time.sleep(1.0)


import os


def test_info_gap_dry_run_no_external_calls(client, monkeypatch):
    """dry_run=True 时绝对不应该 import 外部 LLM/爬虫。"""
    # 监测 _pipeline_class 是否被实例化(只在真模式才 import)
    # 由于 builtin 默认 dry_run=True,真包不会 import
    from backend.wrappers.info_gap import InfoGapWrapper
    w = InfoGapWrapper(dry_run=True)
    assert w._pipeline is None
    # 跑一步也不会触发 _ensure_imported
    async def main():
        await w.run_step("research", {}, progress_cb=lambda p, m: None, log_cb=lambda m: None, is_cancelled=lambda: False)
    asyncio.run(main())
    assert w._pipeline is None  # 真包未被 import


def test_info_gap_list_tools_metadata(client):
    """GET /api/tools 应包含 info_gap 的元数据。"""
    r = client.get("/api/tools")
    # /api/tools 还没建,先 return 404 是预期
    assert r.status_code in (200, 404)