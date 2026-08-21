"""R248 prep: 探针 sector-chg 渲染后布局 — flex-wrap 是否触发 + row2/rowH 是否膨胀

第一性原理: sector 格 col1 auto track=45px (R246 max-width 锁定). 现在加
  sector-chg chip (如 "+9.12" 带 padding ≈50px), sector 格 flex-wrap:wrap →
  sector-name + chg 分两行 → row2 变高 → 整卡膨胀. 量实际 wrap 状态 +
  rowH + col1/col2 尺寸.
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
    for _ in range(25):
        await page.wait_for_timeout(800)
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length >= 1"):
            break
        await page.wait_for_timeout(500)

PROBE = r"""() => {
  var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
  var out = [];
  for (var i=0; i<Math.min(10, rows.length); i++) {
    var row = rows[i];
    var secTd = row.querySelector('td:nth-child(3)');
    var chg = secTd.querySelector('.bv-sector-chg');
    var name = secTd.querySelector('.bv-sector-name');
    var r = secTd.getBoundingClientRect();
    var nameR = name.getBoundingClientRect();
    var chgR = chg ? chg.getBoundingClientRect() : null;
    out.push({
      i: i,
      sector: (name.textContent||'').trim(),
      chgTxt: chg ? (chg.textContent||'').trim() : null,
      tdW: Math.round(r.width),
      nameW: Math.round(nameR.width),
      chgW: chgR ? Math.round(chgR.width) : null,
      // 同一行? name 与 chg 的 top 距离
      sameLine: chg ? Math.abs(nameR.top - chgR.top) < 3 : null,
      tdTop: Math.round(r.top), tdH: Math.round(r.height),
      nameTop: Math.round(nameR.top), chgTop: chgR ? Math.round(chgR.top) : null,
      rowH: row.offsetHeight,
      scrollW: secTd.scrollWidth
    });
  }
  return out;
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        for r in d:
            wrap = "" if r['chgTxt'] is None else ("SAME" if r['sameLine'] else "WRAP!")
            print(f"r{r['i']}: '{r['sector']}' chg={r['chgTxt']} tdW={r['tdW']} nameW={r['nameW']} chgW={r['chgW']} {wrap} rowH={r['rowH']} tdH={r['tdH']} tops=({r['nameTop']},{r['chgTop']})")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
