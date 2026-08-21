"""R246 prep: 探针 row2 完整布局 — sector/streak/seal/time 列宽 + 内容占用

第一性原理: R245 后 col1=45px (sector 4 字到底), col5=74px (turnover 101px 单行).
  问: row2 是否存在与 col1 不同 track 的空间? 用 grid-area 对齐无法分 track —
  必须直接量每格 x/w + 内容 scrollW, 找出"有空间但没用上"的格.
  目标: 给 sector-chg (R16 板块涨幅) 找可回归的空间, 或证明它在当前布局必须让位.
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
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length >= 1"):
            break
        await page.wait_for_timeout(500)

PROBE = r"""() => {
  var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
  var row = rows[0];
  var rr = row.getBoundingClientRect();
  var out = [];
  row.querySelectorAll('td').forEach(function(td, i){
    if (i >= 12) return;  // 前 12 格是 grid items
    var r = td.getBoundingClientRect();
    if (r.width === 0) return;
    out.push({nth: i+1, area: getComputedStyle(td).gridArea,
              x: Math.round(r.left - rr.left), w: Math.round(r.width),
              scrollW: td.scrollWidth, text: (td.textContent||'').trim().slice(0,8)});
  });
  return out;
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print("row0 grid cell map (area @ x, w | scrollW | text):")
        for c in d:
            print(f"  {c['area']:<9} x={c['x']:<3} w={c['w']:<3} scrollW={c['scrollW']:<3} '{c['text']}'")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
