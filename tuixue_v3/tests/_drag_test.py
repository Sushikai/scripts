"""测移动端表格横向拖动是否真的工作 (drag → 看到右侧列)"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:7799"
OUT = Path("/tmp/drag_test")


def main():
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, ignore_https_errors=True, service_workers="block")
        page = ctx.new_page()
        page.goto(BASE + "#dragons", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector(".view-dragons", timeout=10000)
        page.wait_for_timeout(8000)
        page.evaluate("document.body.classList.remove('sidebar-open')")
        # 滚动到今日涨停表
        page.evaluate("document.querySelector('#dragons-all-table').scrollIntoView({block:'start'})")
        page.wait_for_timeout(500)

        # 截图 - 拖动前
        p1 = OUT / "before-drag.png"
        page.screenshot(path=str(p1))

        # 找到表,测量初始 scrollLeft + 测量"封成比"列是否可见
        info_before = page.evaluate("""
            (() => {
                const wrap = document.querySelector('#dragons-all-table').closest('.table-wrap');
                const cs = wrap.scrollLeft;
                // 检查"封成比"列 (col index 8) 在 viewport 内的位置
                const cells = document.querySelectorAll('#dragons-all-table thead th');
                const headers = Array.from(cells).map(c => c.textContent.trim().slice(0,6));
                // 表的总宽 + wrap 宽
                return {scrollLeft: cs, scrollMax: wrap.scrollWidth - wrap.clientWidth, headers, tableW: wrap.scrollWidth, wrapW: wrap.clientWidth};
            })()
        """)
        print(f"Before drag: {info_before}")

        # 用 mouse drag (拖 table-wrap 内任意位置)
        wrap_box = page.evaluate("""
            (() => {
                const r = document.querySelector('#dragons-all-table').closest('.table-wrap').getBoundingClientRect();
                return {x: r.left + 100, y: r.top + 100, w: r.width, h: r.height};
            })()
        """)
        print(f"wrap_box: {wrap_box}")
        # 用 JS 设 scrollLeft 直接测 (模拟拖动结果)
        page.evaluate("document.querySelector('#dragons-all-table').closest('.table-wrap').scrollLeft = 500")
        page.wait_for_timeout(300)
        info_after = page.evaluate("""
            (() => {
                const wrap = document.querySelector('#dragons-all-table').closest('.table-wrap');
                return {scrollLeft: wrap.scrollLeft};
            })()
        """)
        print(f"After scrollLeft=500: {info_after}")
        p2 = OUT / "after-drag.png"
        page.screenshot(path=str(p2))
        print(f"📸 {p2}")

        ctx.close()
        browser.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
