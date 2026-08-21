"""R206: mobile sector-bar padding-bottom 4→2 — 板块条底部 padding 收紧

第一性原理: sector-bar pb=4 (R181). 紧跟下方 pickCard pt=6 (R184), 4+6=10 垂直间距偏松.
  跟 R195 sector-bar pt 0 + R201 filter-bar pb 0 顶部节奏统一, pb 4→2.
  sector-bar 36→34 (-2px). 推票卡下沉 2px, 但 bv-row 首卡完整可见 (R94 守护).

断言 (真实服务, 390px):
  1. sector-bar padding-bottom 2px (从 4px)
  2. sector-bar padding-top 0px 不变
  3. sector-bar h 34 (从 36)
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

        assert d['sb']['pb'] == '2px', f"R206: sb pb={d['sb']['pb']} != 2px"
        assert d['sb']['pt'] == '0px', f"R206: sb pt={d['sb']['pt']} 应仍 0px"
        assert abs(d['sb']['h'] - 34) < 1.5, f"R206: sb h={d['sb']['h']} 应 ~34"
        assert d['pill']['h'] >= 32, f"R206: pill h={d['pill']['h']} 应 >= 32 (R160 tap zone)"

        await b.close()
        print(f"[OK] R206 sector-bar pb 4→2 — sector-bar 36→34 (-2px) 顶部节奏统一 ✓")

if __name__ == "__main__":
    asyncio.run(run())