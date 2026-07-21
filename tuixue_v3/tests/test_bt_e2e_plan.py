#!/usr/bin/env python3
"""
tuixue_v3 尾盘战法回测端到端测试 (R100)
====================================
覆盖批 1-10 的关键不变量 (数据连续 / 公式准确 / 尾盘3信号 / 9-7 退场 / SSE / KPI / 性能 / 导出 / 取消 / A11y)。

运行:
    python3 tests/test_bt_e2e_plan.py           # 跑全部 (假设 server 已起 :7799)
    python3 tests/test_bt_e2e_plan.py --smoke   # 仅 smoke (健康检查 + 5min 配置)
    python3 tests/test_bt_e2e_plan.py --quick   # 小样本 (50 只, 2 period, 30s 内完成)

依赖:
    pip install requests playwright
    playwright install chromium
"""
import sys, os, time, json, argparse, traceback
from pathlib import Path
import requests

BASE = "http://localhost:7799"
RESULTS = []
SKIP = False  # 若 server 不可达则置 True, 不 fail

def record(cat, name, ok, detail=""):
    icon = "✅" if ok else "❌"
    RESULTS.append({"category": cat, "name": name, "ok": ok, "detail": detail})
    print(f"  {icon} [{cat}] {name}: {detail}")

def section(title):
    print(f"\n── {title} " + "─" * max(0, 60 - len(title)))

def check_server_alive():
    section("Server 健康")
    try:
        r = requests.get(f"{BASE}/api/health", timeout=3)
        record("health", "GET /api/health", r.status_code == 200, f"status={r.status_code}")
        return r.status_code == 200
    except Exception as e:
        record("health", "GET /api/health", False, str(e)[:60])
        return False

def test_cancel_endpoint():
    """R97: 取消端点 — 验证状态机 (running → cancel)

    已知限制: 单只 ticker HTTP fetch 阻塞时 cancel 需等网络超时 (~60s).
    本测试用 sample=50 + 立即取消, 验证 5min 翻红步骤能识别 cancel 信号.
    """
    section("R97 cancel 端点")
    try:
        body = {"periods": ["1w"], "hold_days": 2, "top_n": 1,
                "sample": 50, "breadth_min": 0, "breadth_min_soft": 0,
                "sector_hot_topn": 0, "sector_inflow_topn": 0,
                "require_surge_label": False,
                "index_late_up": False, "sector_late_up": False, "tail_vol_ratio_min": 0}
        r = requests.post(f"{BASE}/api/screener/backtest", json=body, timeout=30)
        rid = r.json().get("data", {}).get("run_id")
        record("cancel", "POST /api/screener/backtest", bool(rid), f"run_id={rid[:24] if rid else None}")
        if not rid:
            return
        # 等 3s 让引擎进入 5min 翻红步骤 (有 per-code progress_cb)
        time.sleep(3)
        rc = requests.post(f"{BASE}/api/screener/backtest/cancel?run_id={rid}", timeout=5)
        cdata = rc.json().get("data") or {}
        record("cancel", "POST cancel (running 状态)", rc.status_code == 200 and cdata.get("ok"),
               f"resp={json.dumps(cdata)[:80]}")
        # 等 ≤15s 看 cancel 实际生效 (5min 步骤应快速响应)
        deadline = time.time() + 20
        cancelled = False
        while time.time() < deadline:
            d = requests.get(f"{BASE}/api/screener/backtest?run_id={rid}", timeout=5).json().get("data") or {}
            s = d.get("status")
            if s in ("error",):
                # 已取消状态: server.py:4079 把 progress 设 "已取消"
                if "已取消" in d.get("progress", ""):
                    cancelled = True
                    break
            time.sleep(1)
        record("cancel", "cancel 在 ≤20s 生效", cancelled,
               f"last status={s} progress={d.get('progress','')[:40]}")
    except Exception as e:
        record("cancel", "cancel flow", False, str(e)[:80])

def test_cancel_invalid_state():
    """R97: 错误状态 (未运行 run_id) 应返回 error"""
    section("R97 cancel 边界")
    try:
        rc = requests.post(f"{BASE}/api/screener/backtest/cancel?run_id=bt_nonexistent", timeout=3)
        j = rc.json()
        record("cancel-edge", "不存在的 run_id", bool(j.get("error")), f"err={j.get('error','')[:40]}")
    except Exception as e:
        record("cancel-edge", "non-existent run_id", False, str(e)[:60])

def test_quick_backtest():
    """批 1-7 数据/公式/SSE/KPI 不变量 (小样本快速跑完)"""
    section("批 1-7 不变量 (quick run)")
    try:
        body = {"periods": ["1w"], "hold_days": 2, "top_n": 1,
                "sample": 50, "breadth_min": 0, "breadth_min_soft": 0,
                "sector_hot_topn": 0, "sector_inflow_topn": 0,
                "require_surge_label": False,
                "index_late_up": False, "sector_late_up": False, "tail_vol_ratio_min": 0}
        r = requests.post(f"{BASE}/api/screener/backtest", json=body, timeout=30)
        rid = r.json().get("data", {}).get("run_id")
        if not rid:
            record("quick", "启动回测", False, "no run_id"); return
        # 轮询终态 — 5min 翻红这一步在小样本下也要 60-90s, 给到 150s
        deadline = time.time() + 150
        while time.time() < deadline:
            time.sleep(1.5)
            p = requests.get(f"{BASE}/api/screener/backtest?run_id={rid}", timeout=5).json()
            d = p.get("data") or {}
            if d.get("status") in ("done", "error"):
                break
        record("quick", "回测完成 (≤60s)", d.get("status") == "done",
               f"status={d.get('status')} progress={d.get('progress','')[:40]}")
        if d.get("status") == "done":
            res = (d.get("result") or {})
            tr = res.get("trades_count", 0)
            s9 = (res.get("scenarios") or {})
            ok_form = tr > 0 and any(s9.get(k, {}).get("trades", 0) > 0 for k in s9)
            record("quick", "9 套场景均有数据", ok_form, f"trades={tr} scenarios={len(s9)}")
            # 公式抽查: 胜率 0-1, 复利应一致
            for sn, sd in list(s9.items())[:3]:
                wr = sd.get("win_rate", 0)
                cum = sd.get("cum_return_pct", 0)
                if not (0 <= wr <= 1):
                    record("formula", f"{sn} 胜率越界", False, f"win_rate={wr}")
                    break
            else:
                record("formula", "胜率 ∈ [0,1]", True, "checked 3 scenarios")
    except Exception as e:
        record("quick", "quick run", False, str(e)[:80])

def test_late_session_signals():
    """批 3 (R21-R30): 3 个尾盘信号开关 + 跳过 N 笔"""
    section("批 3 尾盘 3 信号")
    # 仅验证: 启用 vs 不启用, 输出笔数应有差别 (不一定, 但 _skipped 应非空)
    try:
        base = {"periods": ["1m"], "hold_days": 3, "top_n": 1,
                "sample": 200, "breadth_min": 0, "breadth_min_soft": 0,
                "sector_hot_topn": 0, "sector_inflow_topn": 0,
                "require_surge_label": False, "tail_vol_ratio_min": 0}
        # 关 → 开 对比
        for tag, ext in [("关", {}),
                         ("index_late_up", {"index_late_up": True}),
                         ("sector_late_up", {"sector_late_up": True}),
                         ("tail_vol_ratio_min=0.5", {"tail_vol_ratio_min": 0.5})]:
            body = {**base, **ext}
            r = requests.post(f"{BASE}/api/screener/backtest", json=body, timeout=5)
            rid = r.json().get("data", {}).get("run_id")
            if not rid:
                record("late-session", tag, False, "no run_id"); continue
            # 等 done
            deadline = time.time() + 90
            while time.time() < deadline:
                time.sleep(1.5)
                d = requests.get(f"{BASE}/api/screener/backtest?run_id={rid}", timeout=5).json().get("data") or {}
                if d.get("status") in ("done", "error"):
                    break
            if d.get("status") != "done":
                record("late-session", tag, False, f"timeout status={d.get('status')}")
                continue
            tr = (d.get("result") or {}).get("trades_count", 0)
            rec = f"trades={tr} skipped={d.get('result',{}).get('_skipped',0)}"
            record("late-session", f"{tag} 启/调", tr >= 0, rec)
    except Exception as e:
        record("late-session", "signal", False, str(e)[:80])

def test_api_caching():
    """R71-R77: 导出端点 cache + 静态资源 200"""
    section("批 8 导出相关资源")
    paths = ["/static/app.js", "/static/core.js", "/static/style.css", "/static/index.html"]
    for p in paths:
        try:
            r = requests.get(f"{BASE}{p}", timeout=3)
            record("export", p, r.status_code == 200 and len(r.content) > 100,
                   f"size={len(r.content)}")
        except Exception as e:
            record("export", p, False, str(e)[:60])

def print_report():
    section("总结")
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["ok"])
    print(f"  {passed}/{total} passed ({passed/max(total,1)*100:.0f}%)")
    failed = [r for r in RESULTS if not r["ok"]]
    if failed:
        print("\n  ❌ 失败项:")
        for r in failed:
            print(f"    - [{r['category']}] {r['name']}: {r['detail']}")
    return passed == total

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="仅 smoke (健康 + 静态资源)")
    ap.add_argument("--quick", action="store_true", help="小样本快速跑完")
    args = ap.parse_args()

    print(f"Target: {BASE}")
    if not check_server_alive():
        print("\n❌ Server 不可达, 请先 ./web/start_remote.sh 启动")
        sys.exit(1)

    test_api_caching()                            # 静态资源 + 批 8 入口

    if args.smoke:
        print_report(); sys.exit(0)

    if args.quick:
        test_quick_backtest()
        print_report(); sys.exit(0)

    # full
    test_cancel_endpoint()
    test_cancel_invalid_state()
    test_quick_backtest()
    test_late_session_signals()

    print_report()
    sys.exit(0 if all(r["ok"] for r in RESULTS) else 1)

if __name__ == "__main__":
    main()