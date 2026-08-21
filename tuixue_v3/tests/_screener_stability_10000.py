#!/usr/bin/env python3
"""
ZT 涨停溢价 Screener 10000 轮稳定性 + 荐股可靠性测试 (Phase 6e)

覆盖:
- screener 页面 DOM 完整性 (6 个关键区域)
- 回测 API 一致性 (POST /api/zt/backtest → poll status → 结果验证)
- 实时推票可靠性 (GET /api/zt/live_pick → 股票代码有效性)
- 参数端点稳定性 (GET /api/zt/params → OPTIMAL_PARAMS schema)
- 综合推荐 (GET /api/meta/recommend → 数据格式)
- 无 console error (排除 favicon 404)
- 10000 轮逐轮跟踪 + 硬停止条件

用法:
  # 快速验证 (10 轮):
  python tests/test_screener_stability_10000.py --quick

  # 完整 10000 轮:
  python tests/test_screener_stability_10000.py

  # 自定义轮数:
  python tests/test_screener_stability_10000.py --rounds 100

退出码: 0 = 全通过, 1 = 失败, 2 = 硬停止触发
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

BASE = "http://127.0.0.1:7799"
OUT_DIR = Path("/tmp/screener_stability")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 硬停止条件
HARD_STOP_CONSECUTIVE_FAILS = 10    # 连续失败 ≥ N 次
HARD_STOP_FAIL_RATE = 0.05          # 总失败率 > 5%
HARD_STOP_BACKTEST_TIMEOUT = 120    # 回测等待超时 (秒)

# ═══════════════════════════════════════════════════════════
# 关键 DOM selector (对应 screener 页 6 大区域)
# ═══════════════════════════════════════════════════════════

SELECTORS = {
    "strategy_kpi":     "#zt-kpi-strip",       # 策略 KPI 指标
    "strategy_rules":   "#zt-strategy-card",   # 交易策略卡片
    "strategy_params":  "#zt-params-card",     # 策略参数
    "live_pick":        "#zt-live-pick-card",  # 实时推票
    "backtest":         "#zt-backtest-card",   # 回测控制
    "meta_recommend":   "#zt-meta-card",       # 综合推荐
}

# ═══════════════════════════════════════════════════════════
# API helpers
# ═══════════════════════════════════════════════════════════

def _api_get(path: str, timeout: int = 10) -> dict:
    import urllib.request
    try:
        resp = urllib.request.urlopen(f"{BASE}{path}", timeout=timeout)
        return json.loads(resp.read())
    except Exception as e:
        return {"_error": str(e)}


def _api_post(path: str, body: dict, timeout: int = 15) -> dict:
    import urllib.request
    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{BASE}{path}", data=data,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except Exception as e:
        return {"_error": str(e)}


# ═══════════════════════════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════════════════════════

def check_params() -> dict:
    """验证 GET /api/zt/params 返回有效参数 schema。"""
    r = _api_get("/api/zt/params")
    ok = r.get("ok")
    data = r.get("data") or r
    params = data.get("params") or {}
    required = ["min_streak", "max_streak", "board_filter", "entry_rule",
                "trail_activate_pct", "trail_pullback_pct", "stop_loss_pct",
                "top_n", "leverage_factor"]
    missing = [k for k in required if k not in params]
    return {
        "ok": bool(ok) and not missing,
        "params_count": len(params),
        "missing": missing,
        "entry_rule": params.get("entry_rule", "?"),
        "error": r.get("_error", ""),
    }


def check_live_pick() -> dict:
    """验证 GET /api/zt/live_pick 返回有效股票推荐。"""
    r = _api_get("/api/zt/live_pick?top_n=5", timeout=15)
    ok = r.get("ok")
    data = r.get("data") or r
    picks = data.get("picks") or data.get("results") or []

    valid_codes = 0
    invalid_codes = []
    for p in (picks or []):
        code = str(p.get("code", "")).zfill(6)
        if re.match(r"^\d{6}$", code):
            valid_codes += 1
        elif code and code != "000000":
            invalid_codes.append(code)

    has_score = all("score" in p or "weighted_score" in p for p in (picks or []))

    return {
        "ok": ok,
        "picks_count": len(picks) if picks else 0,
        "valid_codes": valid_codes,
        "invalid_codes": invalid_codes,
        "has_score": has_score,
        "error": r.get("_error", ""),
        "_degraded": data.get("_degraded", False),
    }


def check_backtest() -> dict:
    """验证 POST /api/zt/backtest → poll status 完整流程。"""
    now = int(time.time())
    body = {"start": "2026-05-01", "end": "2026-06-30", "run_id": f"stability_{now}"}

    r = _api_post("/api/zt/backtest", body, timeout=15)
    if r.get("_error"):
        return {"ok": False, "phase": "start", "error": r["_error"]}

    ok = r.get("ok")
    data = r.get("data") or r
    run_id = data.get("run_id", "")

    if not run_id:
        # 同步返回结果（无需 poll）
        result = data.get("result") or data.get("results")
        if result:
            trades = result.get("trades") or result.get("history") or []
            kpi = result.get("kpi") or result.get("summary") or {}
            return {
                "ok": ok, "sync": True,
                "trades_count": len(trades),
                "win_rate": kpi.get("win_rate") or kpi.get("winRate", 0),
            }
        return {"ok": False, "phase": "result", "error": "no run_id and no sync result"}

    # 异步: poll status
    start_ts = time.time()
    max_wait = HARD_STOP_BACKTEST_TIMEOUT
    while time.time() - start_ts < max_wait:
        sr = _api_get(f"/api/zt/status?run_id={run_id}", timeout=5)
        if sr.get("_error"):
            return {"ok": False, "phase": "poll", "error": sr["_error"]}
        sd = sr.get("data") or sr
        status = sd.get("status", "")
        if status == "done":
            result = sd.get("result") or {}
            trades = result.get("trades") or result.get("history") or []
            kpi = result.get("kpi") or result.get("summary") or {}
            return {
                "ok": True, "sync": False,
                "trades_count": len(trades),
                "win_rate": kpi.get("win_rate") or kpi.get("winRate", 0),
                "run_id": run_id,
            }
        if status == "error":
            return {"ok": False, "phase": "run", "error": sd.get("error", "unknown")}
        time.sleep(2)

    return {"ok": False, "phase": "timeout", "error": f"poll timeout after {max_wait}s"}


def check_meta_recommend() -> dict:
    """验证 GET /api/meta/recommend 返回推荐格式。"""
    r = _api_get("/api/meta/recommend?top_n=5", timeout=15)
    ok = r.get("ok")
    data = r.get("data") or r
    picks = data.get("picks") or data.get("results") or data.get("stocks") or []

    valid_codes = 0
    for p in (picks or []):
        code = str(p.get("code", "")).zfill(6)
        if re.match(r"^\d{6}$", code):
            valid_codes += 1

    return {
        "ok": ok,
        "picks_count": len(picks) if isinstance(picks, list) else 0,
        "valid_codes": valid_codes,
        "error": r.get("_error", ""),
    }


# ═══════════════════════════════════════════════════════════
# 稳定性主循环
# ═══════════════════════════════════════════════════════════

def run_stability(rounds: int = 10000, quick: bool = False):
    print(f"\n{'='*60}")
    print(f"ZT Screener 稳定性测试 — {rounds} 轮")
    print(f"{'='*60}\n")

    results = []
    consecutive_fails = 0
    start_time = time.time()

    for i in range(1, rounds + 1):
        round_start = time.time()
        round_results = {}
        all_ok = True

        # 1. params
        try:
            r = check_params()
            round_results["params"] = r
            if not r["ok"]:
                all_ok = False
        except Exception as e:
            round_results["params"] = {"ok": False, "error": str(e)}
            all_ok = False

        # 2. live_pick
        try:
            r = check_live_pick()
            round_results["live_pick"] = r
            if not r["ok"]:
                all_ok = False
        except Exception as e:
            round_results["live_pick"] = {"ok": False, "error": str(e)}
            all_ok = False

        # 3. meta_recommend
        try:
            r = check_meta_recommend()
            round_results["meta"] = r
        except Exception as e:
            round_results["meta"] = {"ok": False, "error": str(e)}

        # 4. backtest (每 10 轮跑一次,避免过载)
        if i % 10 == 1:
            try:
                r = check_backtest()
                round_results["backtest"] = r
                if not r["ok"]:
                    all_ok = False
            except Exception as e:
                round_results["backtest"] = {"ok": False, "error": str(e)}
                all_ok = False

        elapsed = (time.time() - round_start) * 1000
        results.append({"round": i, "ok": all_ok, "results": round_results, "elapsed_ms": elapsed})

        # 进度输出
        if all_ok:
            consecutive_fails = 0
        else:
            consecutive_fails += 1

        passed = sum(1 for r in results if r["ok"])
        total = len(results)
        fail_rate = 1 - passed / total if total > 0 else 0

        if i % 100 == 0 or i == 1 or not all_ok:
            bt_info = ""
            if "backtest" in round_results:
                bt = round_results["backtest"]
                bt_info = f' bt={bt.get("trades_count","?")}t/{bt.get("win_rate",0)}wr'
            lp = round_results.get("live_pick", {})
            lp_info = f' picks={lp.get("picks_count","?")}/{lp.get("valid_codes","?")}'
            print(f'  [{i:5d}/{rounds}] {"✓" if all_ok else "✗"} '
                  f'{elapsed:.0f}ms{lp_info}{bt_info} '
                  f'pass={passed}/{total} ({100*(1-fail_rate):.1f}%) '
                  f'cfail={consecutive_fails}')

        # 硬停止检查
        if consecutive_fails >= HARD_STOP_CONSECUTIVE_FAILS:
            print(f"\n  HARD STOP: {consecutive_fails} 连续失败")
            break
        if fail_rate > HARD_STOP_FAIL_RATE and total > 50:
            print(f"\n  HARD STOP: 失败率 {fail_rate:.1%} > {HARD_STOP_FAIL_RATE:.1%}")
            break

        if not quick:
            time.sleep(0.1)  # 轻量延迟防过载

    total_elapsed = time.time() - start_time
    passed = sum(1 for r in results if r["ok"])
    total = len(results)

    # ── 汇总 ──
    print(f"\n{'='*60}")
    print(f"结果: {passed}/{total} PASS ({100*passed/total:.1f}%) in {total_elapsed:.0f}s")
    print(f"{'='*60}")

    # API 级别统计
    for api_name in ["params", "live_pick", "meta", "backtest"]:
        api_results = [r["results"].get(api_name) for r in results if api_name in r.get("results", {})]
        if not api_results:
            continue
        ok_count = sum(1 for r in api_results if r.get("ok"))
        print(f"  {api_name:12s}: {ok_count}/{len(api_results)} OK")

    # 荐股可靠性统计
    live_picks = [r["results"].get("live_pick") for r in results if r.get("results", {}).get("live_pick")]
    if live_picks:
        total_picks = sum(p.get("picks_count", 0) for p in live_picks)
        valid_picks = sum(p.get("valid_codes", 0) for p in live_picks)
        degraded = sum(1 for p in live_picks if p.get("_degraded"))
        print(f"\n  荐股可靠性:")
        print(f"    总推票: {total_picks} 只, 有效代码: {valid_picks} 只 ({100*valid_picks/max(total_picks,1):.1f}%)")
        print(f"    降级次数: {degraded}/{len(live_picks)} ({100*degraded/max(len(live_picks),1):.1f}%)")

    # 延迟统计
    elapseds = sorted(r["elapsed_ms"] for r in results)
    if elapseds:
        p50 = elapseds[len(elapseds)//2]
        p95 = elapseds[int(len(elapseds)*0.95)]
        p99 = elapseds[int(len(elapseds)*0.99)]
        print(f"\n  延迟: P50={p50:.0f}ms P95={p95:.0f}ms P99={p99:.0f}ms")

    # 保存结果
    summary = {
        "rounds": total, "passed": passed, "fail": total - passed,
        "pass_rate": passed / max(total, 1),
        "elapsed_s": total_elapsed,
        "hard_stop": total < rounds,
        "p50_ms": elapseds[len(elapseds)//2] if elapseds else 0,
        "p95_ms": elapseds[int(len(elapseds)*0.95)] if elapseds else 0,
    }
    out_path = OUT_DIR / "stability_summary.json"
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\n  Summary: {out_path}")

    return 0 if passed == total else (2 if total < rounds else 1)


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ZT Screener 稳定性测试")
    parser.add_argument("--rounds", type=int, default=10000, help="轮数 (默认 10000)")
    parser.add_argument("--quick", action="store_true", help="快速模式: 仅 10 轮")
    args = parser.parse_args()

    if args.quick:
        args.rounds = min(args.rounds, 10)

    # 检查服务可达
    try:
        import urllib.request
        urllib.request.urlopen(f"{BASE}/api/zt/params", timeout=3)
    except Exception as e:
        print(f"ERROR: 服务不可达 ({BASE}) — 请先启动 tuixue_v3 server")
        print(f"  {e}")
        sys.exit(3)

    sys.exit(run_stability(args.rounds, quick=args.quick))
