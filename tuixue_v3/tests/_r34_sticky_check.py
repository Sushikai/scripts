"""R34 sticky strip — verify position."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    body { margin: 0; padding: 0; }
    .bv-card { padding: 0; }
    .card-head { padding: 8px 12px; }
    .bv-stale-strip {
      position: sticky; top: 0; z-index: 5;
      padding: 8px 12px; font-weight: 600; font-size: 12px;
      background: rgba(255, 181, 71, 0.12); color: #ffb547;
      border: 1px solid rgba(255, 181, 71, 0.4);
      border-radius: 6px;
    }
    .pick-row { height: 80px; padding: 8px; border-bottom: 1px solid #333; color: #aaa; }
    """
    js = """
    function setStrip() {
      var strip = document.querySelector('.bv-stale-strip');
      strip.hidden = false;
      strip.textContent = '⚠️ 快照 120 秒前 — 数据已陈旧';
    }
    """
    html = f"""<!DOCTYPE html><html><head><style>{css}</style></head>
<body>
<section class="bv-card">
  <div class="card-head">🎯 实时推票</div>
  <div class="bv-stale-strip" id="bv-stale-strip" hidden></div>
  <div class="pick-row">1. 600000 浦发银行 +9.95%</div>
  <div class="pick-row">2. 000001 平安银行 +10.01%</div>
  <div class="pick-row">3. 002142 宁波银行 +9.98%</div>
  <div class="pick-row">4. 600036 招商银行 +9.97%</div>
  <div class="pick-row">5. 000333 美的集团 +9.99%</div>
  <div class="pick-row">6. 000858 五粮液 +9.96%</div>
  <div class="pick-row">7. 600519 贵州茅台 +10.00%</div>
  <div class="pick-row">8. 000002 万科A +9.94%</div>
  <div class="pick-row">9. 601318 中国平安 +9.95%</div>
  <div class="pick-row">10. 600276 恒瑞医药 +9.99%</div>
</section>
<script>{js}</script>
</body></html>"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 600})
        await page.set_content(html)
        await page.evaluate("setStrip()")

        # Initial: strip should be at top of viewport
        rect0 = await page.evaluate("document.querySelector('.bv-stale-strip').getBoundingClientRect()")
        print(f"initial strip rect: top={rect0['top']:.0f}")
        assert rect0['top'] < 100, f"strip should be near top, got {rect0['top']}"

        # Verify position: sticky
        pos = await page.evaluate("getComputedStyle(document.querySelector('.bv-stale-strip')).position")
        top = await page.evaluate("getComputedStyle(document.querySelector('.bv-stale-strip')).top")
        z = await page.evaluate("getComputedStyle(document.querySelector('.bv-stale-strip')).zIndex")
        print(f"computed: position={pos} top={top} z-index={z}")
        assert pos == "sticky", f"position should be sticky, got {pos}"
        assert top == "0px"
        assert z == "5"

        # Scroll down 400px
        await page.evaluate("window.scrollTo(0, 400)")
        await page.wait_for_timeout(100)
        rect1 = await page.evaluate("document.querySelector('.bv-stale-strip').getBoundingClientRect()")
        print(f"after scroll 400px: strip top={rect1['top']:.0f}")
        # strip should be at viewport top (sticky working)
        assert rect1['top'] <= 0 + 5, f"sticky strip should remain at top (≤5px), got {rect1['top']}"

        # Verify still visible
        visible = await page.evaluate("""
          (function(){
            var r = document.querySelector('.bv-stale-strip').getBoundingClientRect();
            return r.top < window.innerHeight && r.bottom > 0;
          })()
        """)
        print(f"visible after scroll: {visible}")
        assert visible

        await page.screenshot(path="/tmp/bv_r34_sticky.png", full_page=True)
        print("[OK] R34 sticky strip works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())