"""验证 stock view 卡片新顺序 (2026-08-09 重排)"""
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:7799"


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(f"PAGEERROR: {e}"))
        page.on("console", lambda m: errors.append(f"{m.type}: {m.text[:150]}") if m.type == "error" else None)

        page.goto(BASE + "?code=600519#stock", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector(".view-stock", timeout=10000)
        page.wait_for_timeout(8000)

        # 1. 验证卡片顺序
        order = page.evaluate("""
          () => {
            const cards = Array.from(document.querySelectorAll('.view-stock > .card, .view-stock > .quote-hero, .view-stock > .quote-bento, .view-stock > .chart-unified'));
            return cards.map(el => {
              const id = el.id || el.className;
              const eyebrow = el.querySelector('.card-eyebrow');
              return {
                tag: el.tagName,
                cls: el.className,
                id: el.id || '',
                eyebrow: eyebrow ? (eyebrow.textContent || '').trim().slice(0, 50) : '',
              };
            }).filter(c => c.eyebrow || c.id);
          }
        """)
        print("=== 卡片顺序 (从上到下) ===")
        for i, c in enumerate(order, 1):
            print(f"  {i}. <{c['tag']}> #{c['id']} eyebrow={c['eyebrow']!r}")

        # 2. 关键 ID 存在性
        must_exist = [
            'stock-quickbar', 'stock-quote-grid', 'q-strategy-match-card',
            'q-streak-host', 'stock-limit-up-card', 'stock-profile-card',
            'q-buypoint-card', 'q-weekly-bull-body', 'q-recovery-body',
            'q-ma5-rules-body', 'stock-deep-analy-card', 'stock-sector-card',
        ]
        print("\n=== 关键 ID 检查 ===")
        for sel in must_exist:
            exists = page.evaluate(f"() => !!document.getElementById('{sel}')")
            print(f"  #{sel}: {'✓' if exists else '✗'}")

        # 3. 验证 mytrades 没有重复
        mytrades_n = page.evaluate("() => document.querySelectorAll('#stock-mytrades-card').length")
        print(f"\nstock-mytrades-card 数量: {mytrades_n} (期望 1)")

        # 4. 综合买点 3 段可见性
        seg_visible = page.evaluate("""
          () => {
            return {
              seg1: !!document.querySelector('#bp-seg-weekly'),
              seg2: !!document.querySelector('#bp-seg-recovery'),
              seg3: !!document.querySelector('#bp-seg-ma5'),
            };
          }
        """)
        print(f"\n综合买点 3 段存在: {seg_visible}")

        # 5. 截图
        page.screenshot(path="/tmp/stock_reorder_full.png", full_page=True)
        page.screenshot(path="/tmp/stock_reorder_viewport.png", full_page=False)

        real_errors = [e for e in errors if "ERR_CONNECTION" not in e and "Service Worker" not in e]
        print(f"\nconsole/page errors (excl conn/sw): {len(real_errors)}")
        for e in real_errors[:5]:
            print(f"   - {e[:180]}")

        browser.close()


if __name__ == "__main__":
    main()