"""用 touch 事件测试移动端拖动"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:7799"
OUT = Path("/tmp/touch_drag")


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
        page.evaluate("document.querySelector('#dragons-all-table').scrollIntoView({block:'start'})")
        page.wait_for_timeout(500)

        # 用 touch dispatch (mobile viewport → has_touch=True → touch events)
        wrap_pos = page.evaluate("""
            (() => {
                const r = document.querySelector('#dragons-all-table').closest('.table-wrap').getBoundingClientRect();
                return {startX: r.left + r.width - 80, startY: r.top + 100, endX: r.left + 30};
            })()
        """)
        print(f"drag from {wrap_pos['startX']} to {wrap_pos['endX']}")
        # 用 page.touchscreen.tap + dispatchTouchEvent 直接派发
        page.evaluate(f"""
            (() => {{
                const el = document.querySelector('#dragons-all-table').closest('.table-wrap');
                el.scrollLeft = 0;
                const startX = {wrap_pos['startX']};
                const startY = {wrap_pos['startY']};
                const endX = {wrap_pos['endX']};
                const touchStart = new Touch({{identifier: 1, target: el, clientX: startX, clientY: startY}});
                el.dispatchEvent(new TouchEvent('touchstart', {{bubbles: true, cancelable: true, touches: [touchStart], targetTouches: [touchStart], changedTouches: [touchStart]}}));
                const steps = 10;
                const dist = startX - endX;
                for (let i = 1; i <= steps; i++) {{
                    const x = startX - dist * i / steps;
                    const touch = new Touch({{identifier: 1, target: el, clientX: x, clientY: startY}});
                    el.dispatchEvent(new TouchEvent('touchmove', {{bubbles: true, cancelable: true, touches: [touch], targetTouches: [touch], changedTouches: [touch]}}));
                }}
                const touchEnd = new Touch({{identifier: 1, target: el, clientX: endX, clientY: startY}});
                el.dispatchEvent(new TouchEvent('touchend', {{bubbles: true, cancelable: true, touches: [], targetTouches: [], changedTouches: [touchEnd]}}));
            }})()
        """)
        page.wait_for_timeout(300)

        scroll_left = page.evaluate("document.querySelector('#dragons-all-table').closest('.table-wrap').scrollLeft")
        print(f"After touch drag, scrollLeft: {scroll_left}")

        p = OUT / "after-touch-drag.png"
        page.screenshot(path=str(p))
        print(f"📸 {p}")
        ctx.close()
        browser.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
