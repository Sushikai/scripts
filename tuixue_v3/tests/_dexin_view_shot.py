"""干净的 dexin view 截图 (桌面 + 移动), sidebar 关闭"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:7799"
OUT = Path("/tmp/dexin_visual")


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        for label, viewport, mobile in [
            ("desktop", {"width": 1280, "height": 800}, False),
            ("mobile", {"width": 390, "height": 844}, True),
        ]:
            ctx = browser.new_context(viewport=viewport, is_mobile=mobile, has_touch=mobile, ignore_https_errors=True)
            page = ctx.new_page()
            page.goto(BASE + "#dexin", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(".view-dexin", timeout=10000)
            page.wait_for_timeout(3000)  # 等数据回来
            page.evaluate("document.body.classList.remove('sidebar-open')")
            p = OUT / f"clean-{label}-dexin.png"
            page.screenshot(path=str(p), full_page=False)
            print(f"📸 {p}")
            # 桌面 only: 切到得鑫主升 tab
            if label == "desktop":
                page.click(".dexin-tab[data-tab='de_xin']")
                page.wait_for_timeout(800)
                p2 = OUT / f"clean-{label}-dexin-de-xin-tab.png"
                page.screenshot(path=str(p2), full_page=False)
                print(f"📸 {p2}")
            ctx.close()
        browser.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
