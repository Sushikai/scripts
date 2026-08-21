"""R214: mobile view-head pt/pb 2→0 — 标题区内 padding 归零

第一性原理: view-head pt/pb=2 (R205). view-actions 36 主导 h=40, pt/pb 2 是额外呼吸.
  跟 R195/R201/R207/R208/R210/R212 顶部节奏统一 (全 0 padding 链), pt/pb 2→0.
  view-head 40→36 (-4px). 标题与 filter-bar 区分靠标题字号 14 vs chip 字号 11.
  refresh-btn tap zone 仍 36 (R127 32+pb4).

断言 (真实服务, 390px):
  1. view-head pt 0px pb 0px (从 2px 2px)
  2. view-head h 36 (从 40)
  3. refresh-btn h 36 不变 (R127 tap zone)
  4. 标题字号 14 不变
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
  var vh = document.querySelector('.view-bv .view-head');
  var refresh = document.querySelector('.view-bv .refresh-btn, .view-bv #bv-refresh');
  return {vh: info(vh), refresh: info(refresh)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"vh: h={d['vh']['h']} pt={d['vh']['pt']} pb={d['vh']['pb']}")
        print(f"refresh: h={d['refresh']['h']}")

        assert d['vh']['pt'] == '0px', f"R214: vh pt={d['vh']['pt']} != 0px"
        assert d['vh']['pb'] == '0px', f"R214: vh pb={d['vh']['pb']} != 0px"
        assert abs(d['vh']['h'] - 36) < 1.5, f"R214: vh h={d['vh']['h']} 应 ~36"
        assert d['refresh']['h'] >= 32, f"R214: refresh h={d['refresh']['h']} 应 >= 32 (R127 tap zone)"

        await b.close()
        print(f"[OK] R214 view-head pt/pb 2→0 — view-head 40→36 (-4px) 顶部全 0 padding 链 ✓")

if __name__ == "__main__":
    asyncio.run(run())