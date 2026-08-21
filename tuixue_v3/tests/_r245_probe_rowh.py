"""R245 prep: 探针 flex-wrap 后行高 102 的构成 — 每 row 实际高度

第一性原理: flex-wrap 让 turnover 换行, row h 75→102. 需知道
  行高增长全来自 turnover 第二行, 还是连带其他 cell 也被拉高
  (grid row 高度 = 该行最高 cell). 若是前者, 说明 2 行是真实成本;
  若有浪费, 可以优化.
"""
import asyncio, json
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
  var row = document.querySelector('#bv-pick-tbody tr.bv-row');
  var rr = row.getBoundingClientRect();
  // 每 td 的 top/bottom → 推断 grid row 边界 (同一 row 的 cell 应共享 top/bottom)
  var tds = row.querySelectorAll('td');
  var info = {};
  tds.forEach(function(td){
    var r = td.getBoundingClientRect();
    if (r.width === 0) return;
    var area = getComputedStyle(td).gridArea;
    if (!info[area]) info[area] = {top: Math.round(r.top - rr.top), bottom: Math.round(r.bottom - rr.top), h: Math.round(r.height)};
  });
  return {rowH: Math.round(rr.height), areas: info};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"rowH={d['rowH']}")
        for k, v in d['areas'].items():
            print(f"  {k:<14} top={v['top']} bottom={v['bottom']} h={v['h']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
