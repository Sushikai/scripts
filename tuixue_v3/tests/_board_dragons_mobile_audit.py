"""board (sector) + dragons 表格 mobile 视觉验证 (修复后)"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:7799"
OUT = Path("/tmp/board_dragons_visual")


def shoot_table(page, label, table_sel, fname):
    """截图 + 量测表"""
    page.evaluate("document.body.classList.remove('sidebar-open')")
    page.wait_for_timeout(300)
    info = page.evaluate(f"""
        (() => {{
            const t = document.querySelector('{table_sel}');
            if (!t) return {{error: 'no table'}};
            const wrap = t.closest('.table-wrap');
            if (!wrap) return {{error: 'no wrap'}};
            const rect = wrap.getBoundingClientRect();
            const tblRect = t.getBoundingClientRect();
            const cs = window.getComputedStyle(wrap);
            // Also measure the parent card
            const card = wrap.closest('.card');
            const cardRect = card ? card.getBoundingClientRect() : null;
            // And the viewport
            const vp = {{w: window.innerWidth, h: window.innerHeight, scrollX: window.scrollX, scrollMaxX: document.documentElement.scrollWidth}};
            return {{
                wrapW: rect.width,
                tableW: tblRect.width,
                canScroll: wrap.scrollWidth > wrap.clientWidth,
                overflowX: cs.overflowX,
                cardW: cardRect ? cardRect.width : null,
                rowCount: t.querySelectorAll('tbody tr').length,
                vp,
            }};
        }})()
    """)
    print(f"[{label}] {fname}: {info}")
    try:
        elem = page.query_selector(table_sel)
        if elem:
            elem.scroll_into_view_if_needed(timeout=5000)
            page.wait_for_timeout(300)
            p = OUT / f"{label}-{fname}.png"
            page.screenshot(path=str(p))
            print(f"📸 {p}")
    except Exception as e:
        print(f"shot err: {e}")


def main():
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        for label, vp in [
            ("mobile_390", {"width": 390, "height": 844}),
            ("mobile_414", {"width": 414, "height": 896}),
            ("tablet_768", {"width": 768, "height": 1024}),
            ("tablet_900", {"width": 900, "height": 1000}),
            ("desktop_1280", {"width": 1280, "height": 800}),
        ]:
            ctx = browser.new_context(viewport=vp, is_mobile=(vp["width"] < 768), has_touch=(vp["width"] < 768), ignore_https_errors=True, service_workers="block")
            page = ctx.new_page()

            # 1. DRAGONS view
            page.goto(BASE + "#dragons", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(".view-dragons", timeout=10000)
            page.wait_for_timeout(8000)  # 等数据
            # 滚到今日涨停表
            try:
                page.evaluate("document.querySelector('#dragons-all-table').scrollIntoView({block:'start'})")
                page.wait_for_timeout(500)
            except Exception:
                pass
            shoot_table(page, label, "#dragons-all-table", "dragons-today")
            # 昨日
            try:
                page.evaluate("document.querySelector('#dragons-yesterday-table').scrollIntoView({block:'start'})")
                page.wait_for_timeout(500)
            except Exception:
                pass
            shoot_table(page, label, "#dragons-yesterday-table", "dragons-yesterday")

            # 2. SECTOR view — 通过 #sector=板块名 hash 路由 (用真实存在的板块)
            page.goto(BASE + "#sector=半导体", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(".view-sector", timeout=10000)
            page.wait_for_timeout(6000)
            try:
                page.evaluate("document.querySelector('#sector-zt-table')?.scrollIntoView({block:'start'})")
                page.wait_for_timeout(500)
            except Exception:
                pass
            shoot_table(page, label, "#sector-zt-table", "sector-zt")
            try:
                page.evaluate("document.querySelector('#sector-5d-table')?.scrollIntoView({block:'start'})")
                page.wait_for_timeout(500)
            except Exception:
                pass
            shoot_table(page, label, "#sector-5d-table", "sector-5d")
            ctx.close()
        browser.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
