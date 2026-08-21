"""R245 prep: 探针 grid 列宽 — turnover 格为何只有 73px

第一性原理: bv-row grid-template-columns = auto minmax(0,1fr) auto auto auto.
  R243 显示 turnover (td5) 高 72px 宽 17px 在 row2 — 但 turnover 3 信号需
  ~117px. 本探针量每列真实像素宽 + 每个 td 的 grid-column span, 定位为什么
  turnover 列那么窄.
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
  if (!row) return null;
  var rr = row.getBoundingClientRect();
  // 量 grid 模板 (row1 各 td 的 left/right → 推断列边界)
  var tds = row.querySelectorAll('td');
  var cells = [];
  tds.forEach(function(td, i){
    var r = td.getBoundingClientRect();
    if (r.width === 0) return;
    var cs = getComputedStyle(td);
    cells.push({n: i+1, left: Math.round(r.left - rr.left), right: Math.round(r.right - rr.left),
                w: Math.round(r.width), gc: cs.gridColumnStart + '→' + cs.gridColumnEnd,
                area: cs.gridArea});
  });
  return cells;
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        for c in d:
            print(f"td{c['n']:>2} {c['area']:<14} x={c['left']:>3}..{c['right']:>3} w={c['w']:>3} gridcol={c['gc']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
