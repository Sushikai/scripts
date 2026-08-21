"""R208: mobile sector-bar padding-bottom 2→0 — 板块条底部 padding 收紧到 chip 底沿

第一性原理: sector-bar pb=2 (R206). 紧跟下方 pickCard pt=6 (R184), 2+6=8 间距仍偏松.
  跟 R207 filter-bar pt 0 pb 0 节奏统一 (顶部两 bar 都是 0 padding), sector-bar pb 2→0.
  sector-bar 34→32 (-2px). pickCard pt 6 给视觉呼吸, sector-bar pb 0 不会让 pill 跟 pickCard 粘连.

断言 (真实服务, 390px):
  1. sector-bar padding-bottom 0px (从 2px)
  2. sector-bar padding-top 0px 不变
  3. sector-bar h 32 (从 34)
  4. 板块 pill 仍 h=32 (R160 tap zone)
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
  var sb = document.querySelector('.view-bv .bv-sector-bar');
  var pill = document.querySelector('.view-bv .bv-sector-pill');
  return {sb: info(sb), pill: info(pill)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"sb: h={d['sb']['h']} pt={d['sb']['pt']} pb={d['sb']['pb']}")
        print(f"pill: h={d['pill']['h']}")

        assert d['sb']['pb'] == '0px', f"R208: sb pb={d['sb']['pb']} != 0px"
        assert d['sb']['pt'] == '0px', f"R208: sb pt={d['sb']['pt']} 应仍 0px"
        assert abs(d['sb']['h'] - 32) < 1.5, f"R208: sb h={d['sb']['h']} 应 ~32"
        assert d['pill']['h'] >= 32, f"R208: pill h={d['pill']['h']} 应 >= 32 (R160 tap zone)"

        await b.close()
        print(f"[OK] R208 sector-bar pb 2→0 — sector-bar 34→32 (-2px) 顶部两 bar 0 padding 节奏统一 ✓")

if __name__ == "__main__":
    asyncio.run(run())