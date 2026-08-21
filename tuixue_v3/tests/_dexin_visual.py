"""R20 视觉回归 — 桌面 1280x800 + 移动 390x844
覆盖:
  - 全 A 表格「得鑫」列截图 (桌面 + 移动)
  - dexin modal 截图 (桌面 + 移动)
  - sidebar 06 位置截图
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:7799"
OUT = Path("/tmp/dexin_visual")
OUT.mkdir(parents=True, exist_ok=True)


def shot(page, name):
    p = OUT / f"{name}.png"
    page.screenshot(path=str(p), full_page=False)
    print(f"  📸 {p}")


def run_desktop(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, ignore_https_errors=True)
    page = ctx.new_page()
    print("=== DESKTOP 1280x800 ===")
    # 1. 首页 sidebar
    page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector("#sidebar", timeout=10000)
    page.wait_for_timeout(1500)
    shot(page, "01-desktop-home")

    # 2. dexin view
    page.goto(BASE + "#dexin", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector(".view-dexin", timeout=10000)
    page.wait_for_timeout(2500)
    shot(page, "02-desktop-dexin")

    # 3. 全 A 表格得鑫列
    page.goto(BASE + "#all_stocks", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector("#as-stocks-tbody", timeout=10000)
    page.wait_for_timeout(25000)
    cnt = page.evaluate("document.querySelectorAll('#as-stocks-tbody td[data-col=\"得鑫\"]').length")
    print(f"  desktop 全 A 得鑫 cells: {cnt}")
    shot(page, "03-desktop-all-stocks")

    # 4. 点 dexin modal 用已知有数据股
    page.evaluate("dexinCheckOne('600519')")
    page.wait_for_selector(".dexin-check-modal", timeout=8000)
    page.wait_for_function(
        "document.querySelector('.dexin-modal-advice') || document.querySelector('.dexin-modal-error')",
        timeout=15000,
    )
    page.wait_for_timeout(500)
    shot(page, "04-desktop-dexin-modal")

    # 5. 个股页搜索 pill 验按钮
    page.goto(BASE + "#stock", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector("#stock-search", timeout=10000)
    page.fill("#stock-search", "600519")
    page.wait_for_timeout(3000)
    shot(page, "05-desktop-search-pill")

    ctx.close()


def run_mobile(browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, ignore_https_errors=True)
    page = ctx.new_page()
    print("=== MOBILE 390x844 ===")
    # 1. 首页
    page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)
    shot(page, "06-mobile-home")

    # 2. sidebar drawer
    page.click("#menu-btn")
    page.wait_for_selector("body.sidebar-open", timeout=5000)
    page.wait_for_timeout(500)
    shot(page, "07-mobile-sidebar")

    # 3. dexin view
    page.goto(BASE + "#dexin", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector(".view-dexin", timeout=10000)
    page.wait_for_timeout(2500)
    shot(page, "08-mobile-dexin")

    # 4. 全 A 表格
    page.goto(BASE + "#all_stocks", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector("#as-stocks-tbody", timeout=10000)
    page.wait_for_timeout(25000)
    cnt = page.evaluate("document.querySelectorAll('#as-stocks-tbody td[data-col=\"得鑫\"]').length")
    print(f"  mobile 全 A 得鑫 cells (DOM): {cnt}")
    visible_cnt = page.evaluate("Array.from(document.querySelectorAll('#as-stocks-tbody td[data-col=\"得鑫\"]')).filter(el => el.offsetParent !== null).length")
    print(f"  mobile 全 A 得鑫 cells (visible): {visible_cnt}")
    shot(page, "09-mobile-all-stocks")

    # 5. dexin modal
    page.evaluate("dexinCheckOne('600519')")
    page.wait_for_selector(".dexin-check-modal", timeout=8000)
    page.wait_for_function(
        "document.querySelector('.dexin-modal-advice') || document.querySelector('.dexin-modal-error')",
        timeout=15000,
    )
    page.wait_for_timeout(500)
    shot(page, "10-mobile-dexin-modal")

    ctx.close()


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        run_desktop(browser)
        run_mobile(browser)
        browser.close()
    print("\n✅ 全部截图完成")
    sys.exit(0)


if __name__ == "__main__":
    main()
