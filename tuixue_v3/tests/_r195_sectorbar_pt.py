"""R195: mobile sector-bar pt 2→0 — 板块条与过滤条间距节奏

第一性原理: sector-bar pt 2 (R181 设, 原 pt=2 跟 filter-bar pb=2 视觉呼吸).
  现在 filter-bar pt=2 (R194) + pb=2, sector-bar 紧贴 filter-bar 下沿.
  sector-bar pt 2→0 让两个 bar 都是 pt=0 (紧贴上沿, 只留 pb 给下方呼吸).
  sector-bar 总高 38→36 (-2px), 跟 filter-bar 36 等高节奏.
  sector-pill tap zone h=32 不受影响.

断言 (真实服务, 390px):
  1. sector-bar padding-top 0px (从 2px)
  2. sector-bar padding-bottom 4px 不变
  3. sector-bar h 36 (从 38)
  4. sector-pill h 32 不变
  5. sector-bar 起点上移 2px
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
    return {h: Math.round(rect.height*10)/10, t: Math.round(rect.top*10)/10, pt: cs.paddingTop, pb: cs.paddingBottom};
  }
  var sectorBar = document.querySelector('.view-bv .bv-sector-bar');
  var pill = document.querySelector('.view-bv .bv-sector-pill');
  return {sectorBar: info(sectorBar), pill: info(pill)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"sectorBar: h={d['sectorBar']['h']} t={d['sectorBar']['t']} pt={d['sectorBar']['pt']} pb={d['sectorBar']['pb']}")
        print(f"pill: h={d['pill']['h']}")

        assert d['sectorBar']['pt'] == '0px', f"R195: sectorBar pt={d['sectorBar']['pt']} != 0px"
        assert d['sectorBar']['pb'] == '4px', f"R195: sectorBar pb={d['sectorBar']['pb']} 应仍 4px"
        assert d['sectorBar']['h'] == 36, f"R195: sectorBar h={d['sectorBar']['h']} 应 == 36"
        assert d['pill']['h'] == 32, f"R195: pill h={d['pill']['h']} 应仍 32"

        await b.close()
        print(f"[OK] R195 sector-bar pt 2→0 — sector-bar 38→36 (-2px), 顶部双 bar pt=0/pb=2 节奏统一 ✓")

if __name__ == "__main__":
    asyncio.run(run())