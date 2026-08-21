"""R211: mobile pickCard padding-top 6→4 — 推票卡顶部 padding 收紧

第一性原理: pickCard pt=6 (R184). 紧跟 sector-bar pb=0 (R208), 中间 0+6=6 间距偏松.
  跟 R202 bv-row pt 4 + R204 bv-row pb 4 + R209 pickCard pb 2 节奏统一, pickCard pt 6→4.
  pickCard headroom 收紧 2px, 顶部 1 卡下沉 2px 让推票更早进入首屏 (R91 守护).

断言 (真实服务, 390px):
  1. pickCard padding-top 4px (从 6px)
  2. pickCard padding-bottom 2px 不变
  3. pickCard 高度 -2px
  4. bv-row 视觉位置不变 (card-head 仍顶部对齐, 距 pickCard 顶 4)
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

        assert d['pickCard']['pt'] == '4px', f"R211: pickCard pt={d['pickCard']['pt']} != 4px"
        assert d['pickCard']['pb'] == '2px', f"R211: pickCard pb={d['pickCard']['pb']} 应仍 2px"

        await b.close()
        print(f"[OK] R211 pickCard pt 6→4 — pickCard 高度 -2px 顶部节奏统一 ✓")

if __name__ == "__main__":
    asyncio.run(run())