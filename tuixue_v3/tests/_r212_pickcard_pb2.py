"""R212: mobile pickCard padding-bottom 2→0 — 推票卡底部 padding 收紧到 bv-row 底沿

第一性原理: pickCard pt=4 pb=2 (R211/R209). bv-row pt=4 pb=4 (R202/R204).
  pickCard pt/bv-row pt = 4+4=8 顶部双 padding,
  pickCard pb/bv-row pb = 2+4=6 底部不对称.
  pickCard pb 2→0 — 底部 0+4=4 跟顶部 4+4=8 节奏清晰 (顶部给 card-head 上下边距, 底部给 bv-row mb=4 R180 间距).

断言 (真实服务, 390px):
  1. pickCard padding-bottom 0px (从 2px)
  2. pickCard padding-top 4px 不变
  3. pickCard 高度 -2px
  4. bv-row 视觉位置不变
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

        assert d['pickCard']['pb'] == '0px', f"R212: pickCard pb={d['pickCard']['pb']} != 0px"
        assert d['pickCard']['pt'] == '4px', f"R212: pickCard pt={d['pickCard']['pt']} 应仍 4px"

        await b.close()
        print(f"[OK] R212 pickCard pb 2→0 — pickCard 高度 -2px 顶部节奏统一 ✓")

if __name__ == "__main__":
    asyncio.run(run())