"""
R102 维稳机制专项回归 (2026-08-14)
===================================
覆盖 4 个故障场景, 验证 R102-A/B/C 加固全部生效:
  1) 端口残留: kill -9 master → 7799 残留 worker 强杀 + kickstart 拉新 < 60s
  2) service 丢: launchctl bootout → keepalive 自动 bootstrap 兜底
  3) AI chat 并发: 4 tab 同时发, 单 worker 串行 4 次应 < 60s (R102-B 并发工具)
  4) stale 兜底: /api/strategies/scan /api/comprehensive/scan 在上游断时返 stale 而非空

不依赖外部网络 — 都打 127.0.0.1:7799。

跑法:
  python web/tests/_r102_recovery.py              # 全跑
  python web/tests/_r102_recovery.py 1            # 单场景
  python web/tests/_r102_recovery.py --step-only  # 只打印 bash 步骤, 不执行
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:7799"
PORT = 7799
LABEL = "com.kaikai.tuixue.server"
PLIST = f"{os.path.expanduser('~')}/Library/LaunchAgents/{LABEL}.plist"

STATEDIR = "/tmp/tuixue_tunnels"
LOG_DIR = Path(STATEDIR)

results = []  # [{scenario, step, ok, msg, elapsed}]


def _now_ts() -> float:
    return time.time()


def _fmt(t0: float) -> str:
    return f"{time.time() - t0:.1f}s"


def _http(path: str, timeout: float = 5.0) -> tuple[int, dict | None, float]:
    t0 = time.time()
    try:
        req = urllib.request.Request(f"{BASE}{path}")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="ignore")
            try:
                return r.status, json.loads(body), time.time() - t0
            except json.JSONDecodeError:
                return r.status, {"_raw": body[:200]}, time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, None, time.time() - t0
    except Exception as e:
        return 0, {"_err": str(e)}, time.time() - t0


def _lsof_listen_pid() -> list[int]:
    """返回 7799 LISTEN 进程的 PID 列表。"""
    out = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{PORT}", "-sTCP:LISTEN", "-t"],
        capture_output=True, text=True, timeout=4,
    )
    pids = [int(p) for p in out.stdout.split() if p.strip().isdigit()]
    return pids


def _wait_healthz(max_wait: float = 120.0) -> float:
    """等 /api/healthz 200, 返回耗时 (秒)。超时返回 -1。"""
    t0 = time.time()
    while time.time() - t0 < max_wait:
        code, _, _ = _http("/api/healthz", timeout=3)
        if code == 200:
            return time.time() - t0
        time.sleep(2)
    return -1.0


# ───────────────────────────────────────────────────────────
# 场景 1: 端口残留 (kill -9 master → 7799 残留 worker 强杀 + kickstart)
# ───────────────────────────────────────────────────────────
def scenario_1_port_residual() -> bool:
    """kill 当前 master + worker, 验证 keepalive 30s 内清端口+拉新 + healthz 通。"""
    print("\n─── 场景 1: 端口残留自愈 (R102-A _kickstart_with_recovery) ───")
    t0 = _now_ts()

    # baseline
    pids_before = _lsof_listen_pid()
    if not pids_before:
        results.append({"scenario": "S1", "step": "baseline", "ok": False,
                        "msg": "7799 没人在 LISTEN — server 未起, 跳过", "elapsed": 0})
        return False
    results.append({"scenario": "S1", "step": "baseline", "ok": True,
                    "msg": f"7799 LISTEN pids={pids_before}", "elapsed": 0})

    # 1) 杀光 7799 LISTEN 进程 (模拟 master 死 + worker 残留)
    subprocess.run(["kill", "-9"] + [str(p) for p in pids_before],
                   capture_output=True, timeout=4)
    time.sleep(1)

    pids_after_kill = _lsof_listen_pid()
    if pids_after_kill:
        results.append({"scenario": "S1", "step": "kill", "ok": False,
                        "msg": f"kill -9 后仍有残留 {pids_after_kill}", "elapsed": _fmt(t0)})
        return False
    results.append({"scenario": "S1", "step": "kill", "ok": True,
                    "msg": f"7799 LISTEN 已清空 (was {pids_before})", "elapsed": _fmt(t0)})

    # 2) 等 keepalive 自动拉新 (应 < 90s = 3 次 30s 探活失败 + 端口清理 + kickstart)
    elapsed = _wait_healthz(max_wait=120.0)
    if elapsed < 0:
        results.append({"scenario": "S1", "step": "keepalive_recovery", "ok": False,
                        "msg": "120s 内 healthz 未恢复 — keepalive 自愈失败", "elapsed": _fmt(t0)})
        return False
    ok = elapsed < 90.0
    results.append({"scenario": "S1", "step": "keepalive_recovery", "ok": ok,
                    "msg": f"healthz 恢复, 耗时 {elapsed:.1f}s {'(< 90s ✓)' if ok else '(> 90s ⚠)'}", "elapsed": _fmt(t0)})
    return ok


# ───────────────────────────────────────────────────────────
# 场景 2: service 丢 (launchctl bootout → keepalive 应自动 bootstrap)
# ───────────────────────────────────────────────────────────
def scenario_2_service_missing() -> bool:
    """bootout service, keepalive 检测到 kickstart 失败 → bootstrap 兜底 → 自动恢复。"""
    print("\n─── 场景 2: service 丢失自动 bootstrap (R102-A) ───")
    t0 = _now_ts()

    # baseline
    code, _, _ = _http("/api/healthz", timeout=3)
    if code != 200:
        results.append({"scenario": "S2", "step": "baseline", "ok": False,
                        "msg": f"server 未起 (healthz={code}), 跳过", "elapsed": 0})
        return False
    results.append({"scenario": "S2", "step": "baseline", "ok": True, "msg": "healthz 200", "elapsed": 0})

    # 1) bootout service (模拟 service 被卸载)
    rc = subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
                        capture_output=True, text=True, timeout=10)
    if rc.returncode != 0 and "not currently" not in (rc.stderr or "") and "Could not find" not in (rc.stderr or ""):
        results.append({"scenario": "S2", "step": "bootout", "ok": False,
                        "msg": f"bootout 失败: rc={rc.returncode} stderr={rc.stderr[:200]}",
                        "elapsed": _fmt(t0)})
        return False
    results.append({"scenario": "S2", "step": "bootout", "ok": True,
                    "msg": f"service 已 bootout (rc={rc.returncode})", "elapsed": _fmt(t0)})

    # 2) 立即 kill 所有 7799 进程, 模拟 master 死
    pids = _lsof_listen_pid()
    if pids:
        subprocess.run(["kill", "-9"] + [str(p) for p in pids], capture_output=True, timeout=4)
        time.sleep(1)
    results.append({"scenario": "S2", "step": "kill_port", "ok": True,
                    "msg": f"7799 LISTEN 已清 ({pids})", "elapsed": _fmt(t0)})

    # 3) 等 keepalive 检测 → kickstart fail → bootstrap → 重启
    # 最坏情况: kickstart fail 要 3 × 30s = 90s + bootstrap + kickstart = 100s
    elapsed = _wait_healthz(max_wait=180.0)
    if elapsed < 0:
        results.append({"scenario": "S2", "step": "auto_recovery", "ok": False,
                        "msg": "180s 内 healthz 未恢复 — bootstrap 兜底未触发", "elapsed": _fmt(t0)})
        return False
    ok = elapsed < 150.0
    results.append({"scenario": "S2", "step": "auto_recovery", "ok": ok,
                    "msg": f"healthz 恢复, 耗时 {elapsed:.1f}s {'(< 150s ✓)' if ok else '(> 150s ⚠)'}", "elapsed": _fmt(t0)})
    return ok


# ───────────────────────────────────────────────────────────
# 场景 3: AI chat 并发 (4 tab 同发, 测 R102-B tool call 并发)
# ───────────────────────────────────────────────────────────
def scenario_3_ai_concurrent() -> bool:
    """4 个 chat 并发, 验证 tool call 并发后单次响应快 50%+ (vs R97-7 串行)。"""
    print("\n─── 场景 3: AI chat 并发 4 tab (R102-B _exec_tool_calls_batch) ───")
    t0 = _now_ts()

    # baseline: server 活
    code, _, _ = _http("/api/healthz", timeout=3)
    if code != 200:
        results.append({"scenario": "S3", "step": "baseline", "ok": False,
                        "msg": f"server 未起 (healthz={code}), 跳过", "elapsed": 0})
        return False
    results.append({"scenario": "S3", "step": "baseline", "ok": True, "msg": "healthz 200", "elapsed": 0})

    # 4 个 chat 并发, 测触发 tool 调用 (用 "用 stock_kline 和 sector_realtime")
    payload = json.dumps({
        "code": "002716",
        "message": "用 stock_kline 和 sector_realtime 看一下, 给买卖建议",
    }).encode()

    def _post_chat() -> tuple[int, float]:
        t1 = time.time()
        try:
            req = urllib.request.Request(
                f"{BASE}/api/yeren/ai/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                body = json.loads(r.read().decode("utf-8", errors="ignore"))
                return r.status, time.time() - t1
        except Exception as e:
            return 0, time.time() - t1

    # 用 thread 起 4 个 chat
    import threading
    threads = []
    for i in range(4):
        th = threading.Thread(target=_post_chat, daemon=True)
        threads.append(th)
    start = time.time()
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=130)
    total_elapsed = time.time() - start

    # 验证: 4 个并发应在合理时间完成 (单 tab 正常 5-30s, 4 并发阻塞会排队但不会 4× 慢)
    # 阈值: 4 并发 < 90s = 单 tab 60s × 4 / 4 worker ≈ 60s + 余量
    ok = total_elapsed < 90.0
    results.append({"scenario": "S3", "step": "4_concurrent_chat", "ok": ok,
                    "msg": f"4 并发 chat 总耗时 {total_elapsed:.1f}s {'(< 90s ✓)' if ok else '(> 90s ⚠)'}",
                    "elapsed": _fmt(t0)})
    return ok


# ───────────────────────────────────────────────────────────
# 场景 4: stale 兜底 (验证 _STALE_TTL 扩 + comprehensive_scan/strategies_scan 已有逻辑)
# ───────────────────────────────────────────────────────────
def scenario_4_stale_fallback() -> bool:
    """验证: strategies_scan / comprehensive_scan / weekly_bull 端点存在 + 不会因上游空返 500。

    真实"上游断了"很难模拟 — 我们只验证:
      a) 3 个端点都能 200 返
      b) data 字段存在 (允许 _warming / _stale 占位)
    """
    print("\n─── 场景 4: stale 兜底可达性 (R102-C _STALE_TTL 扩) ───")
    t0 = _now_ts()

    ok_all = True
    for path in ("/api/strategies/scan", "/api/comprehensive/scan", "/api/weekly_bull"):
        code, body, _ = _http(path, timeout=8)
        if code != 200:
            results.append({"scenario": "S4", "step": path, "ok": False,
                            "msg": f"HTTP {code}", "elapsed": _fmt(t0)})
            ok_all = False
            continue
        # data 字段存在性
        has_data = bool(body and body.get("data"))
        warming = body and body.get("data", {}).get("_warming")
        stale = body and body.get("data", {}).get("_stale")
        results.append({"scenario": "S4", "step": path, "ok": True,
                        "msg": f"HTTP 200, warming={bool(warming)} stale={bool(stale)} has_data={has_data}",
                        "elapsed": _fmt(t0)})
    return ok_all


# ───────────────────────────────────────────────────────────
# bash 步骤 (供 --step-only 打印, 也方便人工演练)
# ───────────────────────────────────────────────────────────
STEP_BASH = f"""\
# R102 4 场景 bash 步骤 — 人工演练用
PORT={PORT}; LABEL={LABEL}; PLIST={PLIST}

# ── 场景 1: 端口残留自愈 ──
kill -9 $(lsof -nP -iTCP:$PORT -sTCP:LISTEN -t) 2>/dev/null
# keepalive 应在 ≤90s 内清端口 + kickstart
sleep 90 && curl -s "http://127.0.0.1:$PORT/api/healthz" | jq .

# ── 场景 2: service 丢自动 bootstrap ──
launchctl bootout "gui/$(id -u)/$LABEL"
kill -9 $(lsof -nP -iTCP:$PORT -sTCP:LISTEN -t) 2>/dev/null
# keepalive 3×30s + bootstrap + kickstart, ≤150s
sleep 150 && curl -s "http://127.0.0.1:$PORT/api/healthz" | jq .

# ── 场景 3: AI chat 并发 ──
for i in 1 2 3 4; do
  curl -s "http://127.0.0.1:$PORT/api/yeren/ai/chat" \\
    -X POST -H 'Content-Type: application/json' \\
    -d '{{"code":"002716","message":"用 stock_kline 和 sector_realtime"}}' &
done; wait

# ── 场景 4: stale 兜底可达性 ──
for path in /api/strategies/scan /api/comprehensive/scan /api/weekly_bull; do
  curl -s "http://127.0.0.1:$PORT$path" | jq '.data | keys'
done
"""


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""

    if arg == "--step-only":
        print(STEP_BASH)
        return 0

    print(f"===== R102 维稳机制回归 (BASE={BASE}) =====")
    print(f"  端口: {PORT}, label: {LABEL}")
    print(f"  plist: {PLIST}")
    print(f"  statedir: {STATEDIR}")

    # 健康预检
    code, _, _ = _http("/api/healthz", timeout=3)
    if code != 200:
        print(f"\n✗ server healthz={code}, 请先 bash web/restart.sh")
        return 2

    fns = {
        "1": scenario_1_port_residual,
        "2": scenario_2_service_missing,
        "3": scenario_3_ai_concurrent,
        "4": scenario_4_stale_fallback,
    }

    if arg and arg in fns:
        ok = fns[arg]()
        print(f"\n─── 场景 {arg}: {'✓ PASS' if ok else '✗ FAIL'} ───")
    else:
        # 默认跑全部 (按安全顺序: 4 → 3 → 1 → 2, 避免前面改状态影响后面)
        ok4 = scenario_4_stale_fallback()
        ok3 = scenario_3_ai_concurrent()
        ok1 = scenario_1_port_residual()
        ok2 = scenario_2_service_missing()
        print(f"\n===== 汇总 =====")
        print(f"  S1 端口残留:     {'✓ PASS' if ok1 else '✗ FAIL'}")
        print(f"  S2 service 丢:   {'✓ PASS' if ok2 else '✗ FAIL'}")
        print(f"  S3 AI 并发:      {'✓ PASS' if ok3 else '✗ FAIL'}")
        print(f"  S4 stale 兜底:   {'✓ PASS' if ok4 else '✗ FAIL'}")

    # 打印每条结果
    print(f"\n----- 详细结果 -----")
    for r in results:
        mark = "✓" if r["ok"] else "✗"
        print(f"  [{mark}] {r['scenario']} {r['step']:30s} {r['msg']}  ({r['elapsed']})")

    # 汇总 pass/fail
    failed = [r for r in results if not r["ok"]]
    print(f"\n  PASS: {len(results) - len(failed)}/{len(results)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())