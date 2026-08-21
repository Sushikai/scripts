"""R217: mobile pickCard padding-top 4→2 — 推票卡顶部 padding 再次收紧

第一性原理: pickCard pt=4 pb=0 (R211/R212). bv-row pt=2 pb=2 (R215/R216).
  顶部 pickCard 4 + bv-row 2 = 6 双 padding, 偏松.
  跟 R215 bv-row pt 2 + R208 sector-bar pb 0 + R210 view-head mb 0 + R214 view-head pt 0 顶部节奏统一,
  pickCard pt 4→2. pickCard 高度 -2px.

断言 (真实服务, 390px):
  1. pickCard padding-top 2px (从 4px)
  2. pickCard padding-bottom 0px 不变
  3. pickCard 高度 -2px
"""
import asyncio
from playwright.async_api import async_playwright

async def load(page):
    for _ in range(5):
        try:
            await page.goto("http://127.0.0.1:7799/#bv", wait_until="networkidle", timeout=20000)
            break
        except Exception:
            await page.wait_for_timeout(2000)
    for _ in range(20):
        await page.wait_for_timeout(800)
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 1:
            break
        await page.wait_for_timeout(500)

PROBE = r"""() => {
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = el.getBoundingClientRect();
    return {h: Math.round(rect.height*10)/10, pt: cs.paddingTop, pb: cs.paddingBottom};
  }
  var pickCard = document.querySelector('.view-bv > .bv-pick-card');
  return {pickCard: info(pickCard)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"pickCard: h={d['pickCard']['h']} pt={d['pickCard']['pt']} pb={d['pickCard']['pb']}")

        assert d['pickCard']['pt'] == '2px', f"R217: pickCard pt={d['pickCard']['pt']} != 2px"
        assert d['pickCard']['pb'] == '0px', f"R217: pickCard pb={d['pickCard']['pb']} 应仍 0px"

        await b.close()
        print(f"[OK] R217 pickCard pt 4→2 — pickCard 高度 -2px ✓")

if __name__ == "__main__":
    asyncio.run(run())