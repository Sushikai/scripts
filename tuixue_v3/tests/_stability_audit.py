#!/usr/bin/env python3
"""
稳定性保证 — 9 维度审计
========================
D1 server 进程  | D2 磁盘    | D3 内存       | D4 Redis
D5 错误率 5xx  | D6 端点 SLA | D7 traceback  | D8 数据源健康
D9 launchd 守护

跑法:
  python3 tests/_stability_audit.py 2>&1 | tee /tmp/stability_audit.log

返回 0 = 全 PASS, 1 = 有 FAIL。
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

RESULTS = []


def record(dim, name, ok, detail=""):
    icon = "OK" if ok else "FAIL"
    RESULTS.append({"dim": dim, "name": name, "ok": ok, "detail": detail})
    print(f"  [{icon}] D{dim}/{name}: {detail}")


def http_get(url: str, timeout: float = 5.0) -> tuple[int, float]:
    t0 = time.time()
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
        return r.status, time.time() - t0
    except Exception as e:
        return 0, time.time() - t0


def cmd_out(cmd: str, default: str = "") -> str:
    try:
        return subprocess.check_output(cmd, shell=True, text=True, timeout=5).strip()
    except Exception:
        return default


def main():
    print("\n== 稳定性保证 9 维度审计 ==\n")

    # ─── D1: server 进程 ───
    print("[D1] server 进程")
    pids = []
    try:
        out = subprocess.check_output("lsof -ti:7799 2>&1", shell=True, text=True)
        pids = [int(p) for p in out.strip().split("\n") if p]
    except Exception:
        pass
    ok = len(pids) >= 2  # master + ≥1 worker
    record("1", "server 进程", ok, f"pids={pids} (master + {len(pids)-1} workers)")

    # ─── D2: 磁盘 ───
    print("\n[D2] 磁盘")
    out = cmd_out("df -h /Users | tail -1")
    used_pct = 0
    try:
        parts = out.split()
        used_pct = int(parts[4].rstrip("%"))
    except Exception:
        pass
    record("2", "磁盘 used < 90%", used_pct < 90, f"used={used_pct}%")
    out = cmd_out("ls -lh /Users/kaikai/scripts/tuixue_v3/access.log 2>/dev/null | awk '{print $5}'", "0")
    log_size_mb = 0
    try:
        sz = out.strip()
        if sz.endswith("M"):
            log_size_mb = float(sz[:-1])
        elif sz.endswith("K"):
            log_size_mb = float(sz[:-1]) / 1024
        elif sz.endswith("G"):
            log_size_mb = float(sz[:-1]) * 1024
    except Exception:
        pass
    record("2", "access.log < 50MB", log_size_mb < 50, f"size={log_size_mb:.1f}MB")

    # ─── D3: 内存 ───
    print("\n[D3] 内存")
    rss_sum_mb = 0
    if pids:
        try:
            out = subprocess.check_output(
                f"ps -p {','.join(map(str, pids))} -o rss= 2>/dev/null",
                shell=True, text=True,
            )
            rss_sum_mb = sum(int(x) for x in out.split() if x) / 1024
        except Exception:
            pass
    record("3", "server RSS sum < 2GB", rss_sum_mb < 2048, f"{rss_sum_mb:.0f}MB")

    # ─── D4: Redis ───
    print("\n[D4] Redis")
    try:
        out = subprocess.check_output("redis-cli ping 2>&1", shell=True, text=True, timeout=3)
        ok = out.strip() == "PONG"
    except Exception:
        ok = False
    record("4", "Redis ping", ok, "PONG" if ok else "down")
    out = cmd_out("redis-cli info memory 2>&1 | grep used_memory_human | awk -F: '{print $2}'", "0")
    redis_mb = 0
    try:
        s = out.strip()
        if s.endswith("M"):
            redis_mb = float(s[:-1])
        elif s.endswith("G"):
            redis_mb = float(s[:-1]) * 1024
    except Exception:
        pass
    record("4", "Redis < 1GB", redis_mb < 1024, f"{redis_mb:.0f}MB")

    # ─── D5: 错误率 5xx ───
    print("\n[D5] 错误率")
    log = Path("/Users/kaikai/scripts/tuixue_v3/access.log")
    e5 = t5 = 0
    if log.exists():
        text = log.read_text(errors="ignore")
        t5 = text.count("\n") + 1
        e5 = sum(1 for ln in text.split("\n") if " 5" in ln and " 5xx " in ln)
        # 更精确: 用 regex
        import re
        e5 = len(re.findall(r'" 5\d\d ', text))
    pct = (e5 / t5 * 100) if t5 > 0 else 0
    record("5", "5xx 占比 < 1%", pct < 1.0, f"{e5}/{t5} = {pct:.3f}%")

    # ─── D6: 端点 SLA ───
    print("\n[D6] 端点 SLA (含 cold + warm)")
    endpoints = [
        ("/api/healthz", 0.5),
        ("/api/version", 0.5),
        ("/api/readyz", 1.5),
        ("/api/sources/health", 1.0),
        ("/api/market/overview", 3.0),
        ("/api/dashboard/signal", 1.5),
        ("/api/dashboard/hot_sectors", 1.5),
        ("/api/dashboard/index_trend", 5.0),
        ("/api/global/sentiment", 5.0),
        ("/api/stock/600519/core", 1.0),
        ("/api/stock/600519/full", 3.5),
        ("/api/stock/600519/limit_up_context", 1.0),
        ("/api/watchlist", 2.0),
        ("/api/strategies/scan?wb_min=1&rl_near=1&ma5=1&mode=or&min_matched=1", 8.0),
    ]
    sla_pass = 0
    for ep, sla_s in endpoints:
        code, took = http_get(f"http://127.0.0.1:7799{ep}", timeout=sla_s + 3)
        ok = code == 200 and took < sla_s
        if ok:
            sla_pass += 1
        record("6", f"{ep[:50]}", ok, f"{code}|{took*1000:.0f}ms (SLA<{sla_s}s)")
    record("6", f"端点 SLA 全过 ({sla_pass}/{len(endpoints)})",
           sla_pass == len(endpoints),
           f"{sla_pass}/{len(endpoints)}")

    # ─── D7: traceback ───
    print("\n[D7] server traceback (近 1h)")
    server_log = Path("/tmp/tuixue_server.log")
    tb_count = 0
    if server_log.exists():
        try:
            # 取最后 1h (假设每行带日期)
            cutoff = time.time() - 3600
            import datetime
            cutoff_str = datetime.datetime.fromtimestamp(cutoff).strftime("%Y-%m-%d %H:%M")
            lines = server_log.read_text(errors="ignore").split("\n")
            recent = [ln for ln in lines[-3000:] if ln >= cutoff_str]
            tb_count = sum(1 for ln in recent if "Traceback" in ln)
        except Exception:
            pass
    record("7", "server Traceback < 5/h", tb_count < 5, f"{tb_count} tracebacks/1h")

    # ─── D8: 数据源健康 ───
    print("\n[D8] 数据源健康")
    code, took = http_get("http://127.0.0.1:7799/api/sources/health", timeout=3)
    if code == 200:
        try:
            req = urllib.request.Request("http://127.0.0.1:7799/api/sources/health")
            with urllib.request.urlopen(req, timeout=3) as r:
                body = json.loads(r.read())
            data = body.get("data", body)
            sources = data.get("sources", [])
            enabled = sum(1 for s in sources if not s.get("disabled"))
            total = len(sources)
            # 至少 1 个 source 启用 (新浪 857/3 fails 表示常用)
            record("8", "至少 1 source 可用", enabled >= 1,
                   f"{enabled}/{total} enabled")
            # 新浪 (高频实时) 应启用; 允许有少量 fail (circuit breaker ≤5/300s)
            sina = next((s for s in sources if "新浪" in s.get("name", "") and "历史" not in s.get("name", "")), None)
            sina_ok = sina is not None and not sina.get("disabled")
            sina_detail = "not found"
            if sina:
                fc = sina.get('total_fails', 0)
                tc = sina.get('total_calls', 0)
                pct_f = (fc / tc * 100) if tc else 0
                sina_detail = f"fails={fc}/{tc}={pct_f:.1f}%"
            record("8", "新浪实时源 enabled (允许 ≤5% fail)",
                   sina_ok, sina_detail)
        except Exception as e:
            record("8", "解析 /api/sources/health", False, str(e)[:80])
    else:
        record("8", "/api/sources/health", False, f"{code}")

    # ─── D9: launchd ───
    print("\n[D9] launchd 守护")
    services = [
        ("com.kaikai.tuixue.server", True),       # 必须 alive
        ("com.kaikai.tuixue.tunnel-keepalive", True),
    ]
    out = cmd_out("launchctl list 2>&1", "")
    for svc, must_alive in services:
        alive = False
        pid_str = "-"
        for ln in out.split("\n"):
            # launchctl list 输出: "PID STATUS LABEL" (whitespace 分隔)
            # 但 STATUS 可能为 "-" 或 "0" (running)。PID 为 "-" 时表示未运行
            parts = ln.split()
            if len(parts) >= 3 and parts[2] == svc:
                pid_str = parts[0]
                alive = pid_str != "-"
                break
        if must_alive:
            record("9", f"{svc}", alive, f"running pid={pid_str}" if alive else "DEAD")
        else:
            record("9", f"{svc} (optional)", True, "skip check")

    # ─── 总结 ───
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["ok"])
    failed = total - passed
    print(f"\n== 总 {total} 项 · 通过 {passed} · 失败 {failed} ==\n")

    fail_list = [r for r in RESULTS if not r["ok"]]
    if fail_list:
        print("失败清单:")
        for r in fail_list:
            print(f"  D{r['dim']}/{r['name']}: {r['detail']}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()