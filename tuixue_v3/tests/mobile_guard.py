"""手机端回归守护 — CLI / watcher / pre-commit

用法:
  python3 tests/mobile_guard.py --once                        # 跑一次 (CI / pre-commit)
  python3 tests/mobile_guard.py --watch                       # 后台 watcher 持续运行
  python3 tests/mobile_guard.py --once --viewport 414         # 自定义宽度
  python3 tests/mobile_guard.py --once --views dash,dragons    # 子集

退出码:
  0  全部 view 绿 + server 健康
  1  任意 view 红 (回归)
  2  server 不可达 (7799 没起)
  3  Playwright / 脚本自身崩溃
"""
import argparse
import json
import os
import sys
import time
import traceback
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _mobile_regression_smoke import run_smoke, VIEWS, BASE as DEFAULT_BASE

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = Path("/tmp/mobile_guard")
DEFAULT_FAIL_AUDIT = REPO_ROOT / "web/static/audit/mobile_fail"

# 改这些文件就触发重跑
WATCH_PATHS = [
    REPO_ROOT / "web/static/style.css",
    REPO_ROOT / "web/static/index.html",
    REPO_ROOT / "web/static/app.js",
    REPO_ROOT / "web/static/core.js",
    REPO_ROOT / "web/static/zt-frontend.js",
    REPO_ROOT / "web/static/view-other.js",
]
for p in REPO_ROOT.glob("web/static/view-*.js"):
    WATCH_PATHS.append(p)


def check_server_health(base: str) -> tuple[bool, str]:
    """GET /api/health → (ok, msg)"""
    try:
        with urllib.request.urlopen(f"{base}/api/health", timeout=5) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if resp.status == 200 and '"ok":true' in body:
                return True, "ok"
            return False, f"health status={resp.status} body={body[:200]}"
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
        return False, f"unreachable: {e}"


def check_index_html(base: str) -> tuple[bool, str]:
    """GET / 不应包含 'index.html missing' (orphaned worker 抢端口的元 bug 标识)"""
    try:
        with urllib.request.urlopen(f"{base}/", timeout=5) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if "index.html missing" in body:
                return False, "GET / returned 'index.html missing' — server worker 可能被孤儿占用,kill 重启"
            if "<app" not in body and "<div id=" not in body:
                return False, "GET / didn't return HTML shell (前 200 字符异常)"
            return True, "ok"
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
        return False, f"unreachable: {e}"


def render_summary(fails: list, total: int, health_ok: bool, health_msg: str,
                   index_ok: bool, index_msg: str) -> str:
    lines = []
    lines.append(f"\n=== mobile_guard SUMMARY ===")
    lines.append(f"  views:        {total - len(fails)}/{total} PASS")
    lines.append(f"  server:       {'✓' if health_ok else '✗'} {health_msg}")
    lines.append(f"  index.html:   {'✓' if index_ok else '✗'} {index_msg}")
    if fails:
        lines.append(f"  failures:")
        for f in fails:
            lines.append(f"    ✗ {f['view']}: {f['issues']}")
            if f.get("page_errs"):
                lines.append(f"        page_errs: {f['page_errs']}")
            if f.get("console_errs"):
                lines.append(f"        console_errs: {f['console_errs']}")
    return "\n".join(lines)


def copy_fails_to_audit(out_dir: Path, fails: list) -> Path:
    """失败截图复制到 web/static/audit/mobile_fail_{ts}/ (用户 git status 容易看到)"""
    if not fails:
        return None
    ts = time.strftime("%Y%m%d_%H%M%S")
    audit = DEFAULT_FAIL_AUDIT / f"mobile_fail_{ts}"
    audit.mkdir(parents=True, exist_ok=True)
    for f in fails:
        src = out_dir / f"{f['view']}.png"
        if src.exists():
            (audit / f"{f['view']}.png").write_bytes(src.read_bytes())
    # 写失败原因 summary
    summary = audit / "failures.txt"
    summary.write_text("\n".join(f"✗ {x['view']}: {x['issues']}" for x in fails), encoding="utf-8")
    return audit


def run_once(viewport: int, views: list, base: str, out_dir: Path) -> int:
    """单次跑;返回 exit code"""
    print(f"[mobile_guard] viewport={viewport} views={','.join(views)} base={base}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) server 健康
    health_ok, health_msg = check_server_health(base)
    if not health_ok:
        print(f"✗ server: {health_msg}")
        print("  → 修: lsof -ti:7799 | xargs kill -9 && cd /Users/kaikai/scripts/tuixue_v3 && bash web/start_remote.sh")
        return 2

    # 2) index.html 没被孤儿抢
    index_ok, index_msg = check_index_html(base)
    if not index_ok:
        print(f"✗ index.html: {index_msg}")
        return 2

    # 3) 14 view 跑
    try:
        fails, total, _ = run_smoke(out_dir, viewport=viewport, views=views, base=base)
    except Exception as e:
        print(f"✗ playwright crash: {e}")
        traceback.print_exc()
        return 3

    # 4) 报告
    print(render_summary(fails, total, health_ok, health_msg, index_ok, index_msg))

    # 5) json 报告
    report = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "viewport": viewport,
        "views": views,
        "total": total,
        "failed": len(fails),
        "server_ok": health_ok,
        "server_msg": health_msg,
        "index_ok": index_ok,
        "index_msg": index_msg,
        "fails": [{"view": f["view"], "issues": f["issues"], "page_errs": f.get("page_errs", []),
                   "console_errs": f.get("console_errs", [])} for f in fails],
    }
    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 6) 失败截图复制到 audit 目录
    if fails:
        audit_dir = copy_fails_to_audit(out_dir, fails)
        if audit_dir:
            print(f"\n[mobile_guard] 失败截图 + summary 已复制到 {audit_dir}")

    return 1 if fails else 0


def watch_loop(viewport: int, views: list, base: str, debounce_sec: float = 5.0, poll_sec: float = 1.0):
    """file watcher:任一 WATCH_PATH mtime 变化 → debounce 后重跑"""
    print(f"[mobile_guard --watch] 监控 {len(WATCH_PATHS)} 文件,debounce={debounce_sec}s poll={poll_sec}s")
    print(f"[mobile_guard --watch] Ctrl+C 退出\n")

    last_mtimes = {p: p.stat().st_mtime if p.exists() else 0 for p in WATCH_PATHS}
    last_change_ts = 0.0

    while True:
        try:
            now = time.time()
            for p in WATCH_PATHS:
                if not p.exists():
                    continue
                mt = p.stat().st_mtime
                if mt != last_mtimes.get(p):
                    last_mtimes[p] = mt
                    last_change_ts = now
                    print(f"[watch] changed: {p.relative_to(REPO_ROOT)}")

            # debounce 窗口内没有新变化 → 触发 rerun
            if last_change_ts > 0 and (now - last_change_ts) >= debounce_sec:
                print(f"\n[mobile_guard --watch] 🔄 文件变更触发重跑 ({time.strftime('%H:%M:%S')})\n")
                last_change_ts = 0.0
                ts = time.strftime("%Y%m%d_%H%M%S")
                out_dir = DEFAULT_OUT / f"watch_{ts}"
                code = run_once(viewport, views, base, out_dir)
                if code == 0:
                    print(f"\n[mobile_guard --watch] ✓ PASS @ {time.strftime('%H:%M:%S')}\n")
                elif code == 2:
                    print(f"\n[mobile_guard --watch] ⚠ SERVER DOWN @ {time.strftime('%H:%M:%S')}\n")
                else:
                    print(f"\n[mobile_guard --watch] ✗ FAIL @ {time.strftime('%H:%M:%S')}\n")

            time.sleep(poll_sec)
        except KeyboardInterrupt:
            print("\n[mobile_guard --watch] bye")
            return
        except Exception as e:
            print(f"[watch] loop err: {e}")
            traceback.print_exc()
            time.sleep(poll_sec)


def main():
    ap = argparse.ArgumentParser(description="手机端回归守护 — Playwright 14 view smoke + server 健康检查")
    ap.add_argument("--once", action="store_true", help="跑一次就退出 (默认模式)")
    ap.add_argument("--watch", action="store_true", help="后台 watcher: 文件变更 → debounce → rerun")
    ap.add_argument("--viewport", type=int, default=390, help="viewport 宽度 (默认 390)")
    ap.add_argument("--views", default=",".join(VIEWS), help="子集,逗号分隔 (默认全部 14 个)")
    ap.add_argument("--base", default=DEFAULT_BASE, help=f"server URL (默认 {DEFAULT_BASE})")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help=f"截图 + 报告目录 (默认 {DEFAULT_OUT})")
    ap.add_argument("--debounce", type=float, default=5.0, help="watch 模式 debounce 秒数 (默认 5)")
    ap.add_argument("--poll", type=float, default=1.0, help="watch 模式 mtime 轮询间隔 (默认 1s)")
    args = ap.parse_args()

    views = [v.strip() for v in args.views.split(",") if v.strip()]
    out_dir = Path(args.out)

    if args.watch:
        watch_loop(args.viewport, views, args.base, args.debounce, args.poll)
    else:
        sys.exit(run_once(args.viewport, views, args.base, out_dir))


if __name__ == "__main__":
    main()