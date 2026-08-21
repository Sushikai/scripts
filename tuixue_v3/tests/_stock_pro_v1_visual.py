"""个股页 R-pro-stock v1.2 视觉验证 — super card 内 7 个 tab (去掉 news/sectors/related)"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:7799"
OUT = Path("/tmp/stock_pro_v1_visual")
OUT.mkdir(exist_ok=True)


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])

        for label, viewport, mobile in [
            ("desktop", {"width": 1280, "height": 900}, False),
            ("mobile",  {"width": 390,  "height": 844}, True),
        ]:
            ctx = browser.new_context(
                viewport=viewport, is_mobile=mobile, has_touch=mobile,
                ignore_https_errors=True, service_workers="block",
            )
            page = ctx.new_page()
            console_msgs = []
            page.on("console", lambda m: console_msgs.append(f"{m.type}: {m.text[:200]}"))
            page.on("pageerror", lambda e: console_msgs.append(f"PAGEERROR: {e}"))

            page.goto(BASE + "?code=600519#stock", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(".view-stock", timeout=10000)
            page.wait_for_timeout(8000)

            # 1. 验证 super card (card 5) 包含分时 + 6 tabs + 10日
            tabs_in_super = page.evaluate("""
              () => {
                const card = document.querySelector('#stock-charts-card');
                if (!card) return null;
                return {
                  tab_count: card.querySelectorAll('.chart-tab').length,
                  tab_tabs: Array.from(card.querySelectorAll('.chart-tab')).map(b => b.dataset.tab),
                  has_intraday_pane: !!card.querySelector('[data-tab-pane=intraday]'),
                  has_streak: !!card.querySelector('#q-streak-10d-card'),
                  standalone_intraday_card: !!document.querySelector('#stock-intraday-card'),
                };
              }
            """)
            print(f"[{label}] super card 状态: {tabs_in_super}")

            # 2. 默认 tab 应该是分时
            active_tab = page.evaluate("""
              () => {
                const t = document.querySelector('.view-stock .chart-tab.active');
                return t ? t.dataset.tab : null;
              }
            """)
            print(f"[{label}] 默认激活 tab: {active_tab}")

            # 3. 各 tab 都可点击
            for t in ['intraday', 'kline', 'flow', 'seats', 'holders', 'crash', 'ai']:
                btn = page.locator(f"button.chart-tab[data-tab='{t}']").first
                btn.click(timeout=5000)
                page.wait_for_timeout(800)
                pane_visible = page.evaluate(f"""
                  () => {{
                    const p = document.querySelector('[data-tab-pane={t}]');
                    return p && !p.hidden;
                  }}
                """)
                print(f"[{label}] tab '{t}' 点击: pane visible = {pane_visible}")

            # 4. 截整页 + super card
            page.screenshot(path=str(OUT / f"{label}_full.png"), full_page=True)

            page.evaluate("document.querySelector('#stock-charts-card')?.scrollIntoView({block:'start'})")
            page.wait_for_timeout(500)
            page.screenshot(path=str(OUT / f"{label}_super_card.png"))

            # 5. 错误日志
            errors = [m for m in console_msgs if "error" in m.lower() or "PAGEERROR" in m]
            real_errors = [e for e in errors if "Service Worker" not in e and "ERR_CONNECTION" not in e]
            print(f"[{label}] console errors (excluding SW/conn): {len(real_errors)}")
            for e in real_errors[:3]:
                print(f"   - {e[:200]}")

            ctx.close()

        browser.close()
        print(f"\n截图保存到 {OUT}")
        print("=" * 60)


if __name__ == "__main__":
    main()