#!/usr/bin/env python3
"""_dexin_loop.py — 循环跑 audit_views + dexin e2e 到连续 N 轮 0 fail。

用法:
    cd /Users/kaikai/scripts/tuixue_v3
    python3 _dexin_loop.py             # 默认 5 轮连续 0 fail 即停
    python3 _dexin_loop.py --target N  # 自定义目标连续 0 fail 轮数
    python3 _dexin_loop.py --max 50    # 最大轮次上限 (默认 100)

输出:
    /tmp/dexin_loop/loop_<ts>.log       每轮详细日志
    /tmp/dexin_loop/loop_<ts>.json      每轮汇总 (pass/fail/error)

退出码:
    0 = 达到目标连续 0 fail
    1 = 达到 max 轮数仍有 fail
    2 = 不可恢复错误 (server 未启动等)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = Path("/tmp/dexin_loop")
OUT.mkdir(parents=True, exist_ok=True)

PYTEST_BIN = "/Users/kaikai/.hermes/hermes-agent/venv/bin/python3"
AUDIT_PY = ROOT / "audit_views.py"
E2E_PY = ROOT / "tests" / "test_dexin_e2e.py"

PASS_LINE_RE = re.compile(r"^\s*pass=(\d+)\s+fail=(\d+)\s+error=(\d+)\s*$", re.M)
PYTEST_PASS_RE = re.compile(r"(\d+)\s+passed", re.I)
PYTEST_FAIL_RE = re.compile(r"(\d+)\s+failed", re.I)


def _check_server() -> bool:
    try:
        import urllib.request
        r = urllib.request.urlopen("http://127.0.0.1:7799/api/healthz", timeout=3)
        return b'"ok":true' in r.read()
    except Exception:
        return False


def _run_audit() -> dict:
    """跑 audit_views.py, 返 {pass, fail, error, dur_s}"""
    t0 = time.time()
    log = OUT / f"audit_{time.strftime('%Y%m%d_%H%M%S')}.log"
    try:
        proc = subprocess.run(
            [PYTEST_BIN, str(AUDIT_PY)],
            cwd=str(ROOT.parent),
            capture_output=True, text=True, timeout=420,
        )
    except subprocess.TimeoutExpired:
        return {"pass": 0, "fail": 0, "error": -1, "dur_s": 420, "raw": "TIMEOUT"}
    dur = time.time() - t0
    out = proc.stdout + "\n" + proc.stderr
    log.write_text(out)
    m = PASS_LINE_RE.search(out)
    if not m:
        return {"pass": 0, "fail": -1, "error": -1, "dur_s": dur, "raw": out[-500:]}
    return {"pass": int(m.group(1)), "fail": int(m.group(2)), "error": int(m.group(3)), "dur_s": dur}


def _run_e2e() -> dict:
    """跑 test_dexin_e2e.py, 返 {passed, failed, dur_s}"""
    t0 = time.time()
    try:
        proc = subprocess.run(
            [PYTEST_BIN, "-m", "pytest", str(E2E_PY), "-v", "--tb=no", "-q"],
            cwd=str(ROOT),
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {"passed": 0, "failed": -1, "dur_s": 300, "raw": "TIMEOUT"}
    dur = time.time() - t0
    out = proc.stdout + "\n" + proc.stderr
    pm = PYTEST_PASS_RE.search(out)
    fm = PYTEST_FAIL_RE.search(out)
    passed = int(pm.group(1)) if pm else 0
    # fm 不匹配意味着无失败 (pytest 全部通过时不输出 "N failed")
    failed = int(fm.group(1)) if fm else 0
    return {"passed": passed, "failed": failed, "dur_s": dur, "raw_tail": out[-300:]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=3, help="连续 0 fail 目标轮数 (默认 3)")
    ap.add_argument("--max", type=int, default=100, help="最大轮数上限 (默认 100)")
    ap.add_argument("--skip-e2e", action="store_true", help="只跑 audit_views")
    ap.add_argument("--skip-audit", action="store_true", help="只跑 e2e (audit 单独跑更稳)")
    args = ap.parse_args()

    if not _check_server():
        print("❌ server 未启动 (127.0.0.1:7799), 请先启服务", file=sys.stderr)
        sys.exit(2)

    print(f"═══ _dexin_loop · 目标连续 {args.target} 轮 0 fail / 上限 {args.max} 轮 ═══")
    print(f"    audit_views: {AUDIT_PY}")
    print(f"    e2e:         {E2E_PY}")
    print(f"    日志:        {OUT}")
    print()

    rounds = []
    streak = 0
    best_round = 0

    for r in range(1, args.max + 1):
        ts = time.strftime("%H:%M:%S")
        print(f"── 轮 {r:3d} [{ts}] ──")
        # 串行跑避免 audit + e2e 同时撞 chromium / sqlite (per memory feedback_tuixue_v3_views_audit_jul26)
        a = _run_audit() if not args.skip_audit else {"pass": 0, "fail": 0, "error": 0, "dur_s": 0}
        e = _run_e2e() if not args.skip_e2e else {"passed": 0, "failed": 0, "dur_s": 0}
        # 等 server 喘息 5s
        time.sleep(5)

        round_ok = (a["fail"] == 0 and a["error"] == 0 and e["failed"] == 0)
        rounds.append({"round": r, "ts": ts, "audit": a, "e2e": e, "ok": round_ok})

        status = "✅" if round_ok else "❌"
        print(f"  audit: pass={a['pass']} fail={a['fail']} error={a['error']} ({a['dur_s']:.0f}s)")
        print(f"  e2e:   pass={e['passed']} fail={e['failed']} ({e['dur_s']:.0f}s)")
        print(f"  {status}\n")

        if round_ok:
            streak += 1
            if streak > best_round:
                best_round = streak
            if streak >= args.target:
                print(f"═══ 连续 {streak} 轮 0 fail · 达到目标 ═══")
                _save(rounds, OUT / f"loop_{time.strftime('%Y%m%d_%H%M%S')}.json")
                sys.exit(0)
        else:
            streak = 0

    print(f"═══ 已达 max={args.max} 轮未稳定 (best streak={best_round}) ═══")
    _save(rounds, OUT / f"loop_{time.strftime('%Y%m%d_%H%M%S')}.json")
    sys.exit(1)


def _save(rounds, path):
    path.write_text(json.dumps(rounds, ensure_ascii=False, indent=2))
    print(f"  日志已存: {path}")


if __name__ == "__main__":
    main()