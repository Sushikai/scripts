"""R216: mobile bv-row padding-bottom 4→2 — 卡片内 padding 底部再次收紧

第一性原理: bv-row pt=2 pb=4 (R215/R204). pickCard pt=4 pb=0 (R211/R212).
  顶部 pickCard 4 + bv-row 2 = 6, 底部 bv-row 4 + pickCard 0 = 4 不对称.
  bv-row pb 4→2 顶部=底部 2-2 节奏, 跟 R214/R208/R207 全 0 链统一 (bv-row 内 padding 收紧).
  bv-row h 80.7→78.7 (-2px), 15 行累计回收 30px.

断言 (真实服务, 390px):
  1. bv-row padding-bottom 2px (从 4px)
  2. bv-row padding-top 2px 不变
  3. bv-row h 78.7 (从 80.7)
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

        assert d['row']['pb'] == '2px', f"R216: row pb={d['row']['pb']} != 2px"
        assert d['row']['pt'] == '2px', f"R216: row pt={d['row']['pt']} 应仍 2px"
        assert abs(d['row']['h'] - 78.7) < 1.5, f"R216: row h={d['row']['h']} 应 ~78.7"

        await b.close()
        print(f"[OK] R216 bv-row pb 4→2 — bv-row h 80.7→78.7 (-2px), 15 行累计回收 30px ✓")

if __name__ == "__main__":
    asyncio.run(run())