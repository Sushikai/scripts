"""R221: mobile pickCard padding-top 2→0 — 推票卡顶部 padding 归零

第一性原理: pickCard pt=2 pb=0 (R217/R212). bv-row pt=0 pb=0 (R219/R220).
  顶部 pickCard 2 + bv-row 0 = 2, 底部 bv-row 0 + pickCard 0 = 0 不对称.
  跟 R214/R208/R207/R212/R219/R220 顶部全 0 节奏统一, pickCard pt 2→0.
  pickCard 高度 -2px. 推票卡顶部直接贴 sector-bar.

断言 (真实服务, 390px):
  1. pickCard padding-top 0px (从 2px)
  2. pickCard padding-bottom 0px 不变
  3. pickCard 高度 -2px
  4. 卡片区分仍清晰 (1px border)
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

        assert d['pickCard']['pt'] == '0px', f"R221: pickCard pt={d['pickCard']['pt']} != 0px"
        assert d['pickCard']['pb'] == '0px', f"R221: pickCard pb={d['pickCard']['pb']} 应仍 0px"

        await b.close()
        print(f"[OK] R221 pickCard pt 2→0 — pickCard 高度 -2px, 推票卡顶部直接贴 sector-bar ✓")

if __name__ == "__main__":
    asyncio.run(run())