"""R42 sticky end footer."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-loadmore-end {
      text-align: center; padding: 16px; font-size: 11px;
      border-top: 1px dashed #333;
      position: sticky; bottom: 0; background: rgba(15,15,18,0.92);
      backdrop-filter: blur(8px); z-index: 4;
    }
    .bv-loadmore-end[hidden] { display: none !important; }
    .pick-row { height: 80px; padding: 8px; border-bottom: 1px solid #333; color: #aaa; }
    """
    html = f"""<!DOCTYPE html><html><head><style>{css}</style></head>
<body>
<section>
  <div class="pick-row">1. 600000</div>
  <div class="pick-row">2. 000001</div>
  <div class="pick-row">3. 002142</div>
  <div class="pick-row">4. 600036</div>
  <div class="pick-row">5. 000333</div>
  <div class="pick-row">6. 000858</div>
  <div class="pick-row">7. 600519</div>
  <div class="pick-row">8. 000002</div>
  <div class="pick-row">9. 601318</div>
  <div class="pick-row">10. 600276</div>
  <div class="bv-loadmore-end" id="bv-loadmore-end">已加载全部 45 只</div>
</section>
</body></html>"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # initial
        pos = await page.evaluate("getComputedStyle(document.querySelector('#bv-loadmore-end')).position")
        bottom = await page.evaluate("getComputedStyle(document.querySelector('#bv-loadmore-end')).bottom")
        bg = await page.evaluate("getComputedStyle(document.querySelector('#bv-loadmore-end')).backgroundColor")
        print(f"computed: position={pos} bottom={bottom} bg={bg}")
        assert pos == "sticky"
        assert bottom == "0px"

        # scroll up — end should stay at bottom of viewport
        await page.evaluate("window.scrollTo(0, 200)")
        await page.wait_for_timeout(100)
        rect = await page.evaluate("document.querySelector('#bv-loadmore-end').getBoundingClientRect()")
        vh = await page.evaluate("window.innerHeight")
        print(f"after scroll 200: rect.bottom={rect['bottom']:.0f} vh={vh}")
        # sticky bottom should keep it at viewport bottom
        assert rect['bottom'] >= vh - 5, f"end should be near viewport bottom, got bottom={rect['bottom']}"

        await page.screenshot(path="/tmp/bv_r42_sticky.png", full_page=True)
        print("[OK] R42 sticky end footer")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())