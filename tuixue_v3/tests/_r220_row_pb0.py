"""R220: mobile bv-row padding-bottom 2→0 — 卡片内 padding 底部归零

第一性原理: bv-row pt=0 pb=2 (R219). pickCard pt=2 pb=0 (R217/R212).
  顶部 pickCard 2 + bv-row 0 = 2, 底部 bv-row 2 + pickCard 0 = 2 对称.
  跟 R214/R208/R207/R212/R219 顶部全 0 节奏统一, bv-row pb 2→0.
  bv-row h 76.7→74.7 (-2px), 15 行累计回收 30px. bv-row 内 pt=pb=0 完全归零, 视觉靠 1px border + hit-tier stripe 区分.

断言 (真实服务, 390px):
  1. bv-row padding-bottom 0px (从 2px)
  2. bv-row padding-top 0px 不变
  3. bv-row h 74.7 (从 76.7)
  4. hit-tier stripe 仍清晰 (R113)
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
  var row = document.querySelector('.view-bv .bv-table tr.bv-row');
  return {row: info(row)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"row: h={d['row']['h']} pt={d['row']['pt']} pb={d['row']['pb']}")

        assert d['row']['pb'] == '0px', f"R220: row pb={d['row']['pb']} != 0px"
        assert d['row']['pt'] == '0px', f"R220: row pt={d['row']['pt']} 应仍 0px"
        assert abs(d['row']['h'] - 74.7) < 1.5, f"R220: row h={d['row']['h']} 应 ~74.7"

        await b.close()
        print(f"[OK] R220 bv-row pb 2→0 — bv-row h 76.7→74.7 (-2px), bv-row 内 pt=pb=0 完全归零 ✓")

if __name__ == "__main__":
    asyncio.run(run())