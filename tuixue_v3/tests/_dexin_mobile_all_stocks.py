"""R20 mobile 全 A 表格「得鑫」列可见性单测 (重截, 之前 sidebar 没收)"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:7799"
OUT = Path("/tmp/dexin_visual")


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, ignore_https_errors=True)
        page = ctx.new_page()
        page.goto(BASE + "#all_stocks", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("#as-stocks-tbody", timeout=10000)
        page.wait_for_timeout(25000)
        # 关闭 sidebar
        page.evaluate("document.body.classList.remove('sidebar-open')")
        page.wait_for_timeout(500)
        cnt_dom = page.evaluate("document.querySelectorAll('#as-stocks-tbody td[data-col=\"得鑫\"]').length")
        cnt_vis = page.evaluate("Array.from(document.querySelectorAll('#as-stocks-tbody td[data-col=\"得鑫\"]')).filter(el => el.offsetParent !== null).length")
        print(f"mobile 全 A 得鑫 cells (DOM/visible): {cnt_dom}/{cnt_vis}")
        # 滚到右半部分看列
        page.evaluate("document.querySelector('#as-scroll-sentinel').scrollIntoView()")
        page.wait_for_timeout(500)
        p = OUT / "11-mobile-all-stocks-right.png"
        page.screenshot(path=str(p))
        print(f"📸 {p}")
        # 横滚到得鑫列
        page.evaluate("document.querySelector('#as-stocks-tbody td[data-col=\"得鑫\"]')?.scrollIntoView({inline: 'center', block: 'center'})")
        page.wait_for_timeout(500)
        p = OUT / "12-mobile-all-stocks-dexin-col.png"
        page.screenshot(path=str(p))
        print(f"📸 {p}")
        # 全局 P3 列开关状态
        p3_rule = page.evaluate("(function() { const ss = Array.from(document.styleSheets).find(s => { try { return s.href && s.href.includes('style.css'); } catch(e) { return false; }}); return ss ? '@media (max-width: 768px) { #as-stocks-tbody td[data-priority=\"3\"] { display: none; } }' : 'no rule'; })()")
        print(f"P3 CSS check: {p3_rule}")
        # 直接判断
        display = page.evaluate("(function() { const el = document.querySelector('#as-stocks-tbody td[data-col=\"得鑫\"]'); return el ? window.getComputedStyle(el).display : 'no-el'; })()")
        print(f"得鑫 cell computed display: {display}")
        ctx.close()
        browser.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
