"""手机端回归冒烟测 — 14 个 view × mobile 390 viewport,捕捉 page-level 横滚 / view 隐藏 / console error

可作为 lib 被 mobile_guard.py 复用,也可独立跑:`python3 tests/_mobile_regression_smoke.py`"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:7799"
VIEWS = ["dash", "dragons", "stock", "watchlist", "all_stocks", "strategy_picker",
         "dexin", "screener", "laws", "review", "sector", "weekly_bull", "optimize", "sources"]


def view_url(view: str, base: str = BASE) -> str:
    if view == "sector":
        return base + "#sector=半导体"
    if view == "stock":
        return base + "?code=600519#stock"
    return base + f"#{view}"


def check_view(page, view: str, base: str = BASE):
    """单 view 检查:返回 {view, issues[], console_errs[], page_errs[]}"""
    ce2 = []
    pe2 = []
    page.on("console", lambda m: ce2.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: pe2.append(str(e)))
    try:
        page.goto(view_url(view, base), wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)
        viewsel = f".view-{view}"
        visible = page.evaluate(f"""
            (() => {{
                const v = document.querySelector('{viewsel}');
                if (!v) return false;
                const r = v.getBoundingClientRect();
                return r.height > 50 && r.width > 0 && !v.hidden;
            }})()
        """)
        vp_issues = page.evaluate("""
            (() => {
                const d = document.documentElement;
                return {
                    scrollX: window.scrollX || window.pageXOffset || 0,
                    scrollMaxX: d.scrollWidth,
                    vpW: window.innerWidth,
                    bodyOverflowX: window.getComputedStyle(document.body).overflowX,
                };
            })()
        """)
        table_issues = page.evaluate("""
            (() => {
                const bugs = [];
                document.querySelectorAll('.table-wrap').forEach(tw => {
                    const r = tw.getBoundingClientRect();
                    const tbl = tw.querySelector('table');
                    if (!tbl) return;
                    const tblR = tbl.getBoundingClientRect();
                    if (tblR.width > tw.clientWidth * 5) {
                        bugs.push({wrap: tw.clientWidth, table: tblR.width});
                    }
                });
                return bugs;
            })()
        """)
        # v238 关键检查:大表必须能内部横滚,且 wrap 不能被父节点裁切到右侧
        table_overflow = page.evaluate(f"""
            (() => {{
                const bugs = [];
                const vpW = window.innerWidth;
                document.querySelectorAll('.table-wrap').forEach(tw => {{
                    const tbl = tw.querySelector('table');
                    if (!tbl) return;
                    const wrapW = tw.clientWidth;
                    const tableW = tbl.getBoundingClientRect().width;
                    const canScroll = tw.scrollWidth > tw.clientWidth + 1;
                    const wrapRect = tw.getBoundingClientRect();
                    // wrap 的右边界不能被裁到 viewport 之外 (说明祖先 overflow 把它切了)
                    const rightEdge = wrapRect.right;
                    if (rightEdge > vpW + 2) {{
                        bugs.push({{view: '{view}', kind: 'wrap-cut-off-right', wrapRight: Math.round(rightEdge), vpW}});
                    }}
                    // wrap 内的 table 比 wrap 宽 ≥30% 且 wrap 不能滚 → 经典 v238 bug
                    if (tableW > wrapW * 1.3 && !canScroll) {{
                        bugs.push({{view: '{view}', kind: 'table-not-scrollable', tableW: Math.round(tableW), wrapW: Math.round(wrapW), scrollW: tw.scrollWidth}});
                    }}
                }});
                return bugs;
            }})()
        """)
        issues = []
        if vp_issues["scrollMaxX"] > vp_issues["vpW"] + 5:
            issues.append(f"page-horiz-scroll scrollMaxX={vp_issues['scrollMaxX']} > vpW={vp_issues['vpW']}")
        if vp_issues["scrollX"] > 0:
            issues.append(f"already-scrolled scrollX={vp_issues['scrollX']}")
        if table_issues:
            issues.append(f"table-blowout {table_issues[:2]}")
        if table_overflow:
            issues.append(f"table-overflow {table_overflow[:2]}")
        if not visible:
            issues.append(f"view-{view} not visible/loaded")
        return {"view": view, "issues": issues, "console_errs": ce2[:3], "page_errs": pe2[:3]}
    except Exception as e:
        return {"view": view, "issues": [f"exception: {e}"], "console_errs": [], "page_errs": []}


def run_smoke(out_dir: Path, viewport: int = 390, views: list = None, base: str = BASE, label_prefix: str = ""):
    """跑全部 view,返回 (fails[], total_count)

    - out_dir: 截图目录 (会自动 mkdir)
    - viewport: viewport 宽度 (默认 390)
    - views: 子集;None = 全部 14 个
    - base: server URL
    - label_prefix: 截图文件名前缀
    """
    out_dir.mkdir(exist_ok=True)
    views = views or VIEWS
    is_mobile = viewport < 768
    fails = []
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": viewport, "height": 844},
                                  is_mobile=is_mobile, has_touch=is_mobile,
                                  ignore_https_errors=True, service_workers="block")
        page = ctx.new_page()
        for v in views:
            r = check_view(page, v, base)
            page.evaluate("document.body.classList.remove('sidebar-open')")
            page.wait_for_timeout(200)
            shot_name = f"{label_prefix}{v}.png"
            try:
                page.screenshot(path=str(out_dir / shot_name))
            except Exception:
                pass
            status = "✓" if not r["issues"] else "✗"
            print(f"{status} {r['view']}: {r['issues']}")
            results.append(r)
            if r["issues"] or r["page_errs"]:
                fails.append(r)
        ctx.close()
        browser.close()
    return fails, len(views), results


def main():
    out = Path("/tmp/mobile_smoke")
    fails, total, _ = run_smoke(out)
    print(f"\n=== TOTAL: {len(fails)}/{total} failed ===")
    if fails:
        for f in fails:
            print(f"  ✗ {f['view']}: {f['issues']}, page_errs={f['page_errs']}, console_errs={f['console_errs']}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()