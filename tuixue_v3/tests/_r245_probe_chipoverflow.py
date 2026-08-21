"""R245 prep: 探针 rules-cell 内 chips 是否溢出卡片下边界

第一性原理: R105 设 rule chip 触控热区 32px, 但 _r243 probe 显示
  rules-cell (td10) 高 28px, bv-row mb=0 (R218). 若 chip 实际 32px,
  会溢出卡片底边 4px 叠到下一张卡 — 视觉瑕疵.
  本探针量 chip 真实盒高 vs 卡片底边界, 找溢出.
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
  var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
  var out = [];
  for (var i=0; i<Math.min(3, rows.length); i++) {
    var r = rows[i];
    var rr = r.getBoundingClientRect();
    var rules = r.querySelector('td:nth-child(10)');
    var rr2 = rules ? rules.getBoundingClientRect() : null;
    // 规则 cell 内所有 chip/badge 的盒
    var chips = [];
    if (rules) rules.querySelectorAll('span').forEach(function(s){
      var sr = s.getBoundingClientRect();
      chips.push({cls: (s.className||'').slice(0,24), top: Math.round(sr.top), bottom: Math.round(sr.bottom),
                  h: Math.round(sr.height), text: (s.textContent||'').trim().slice(0,10)});
    });
    var overflow = 0;
    if (rr2) chips.forEach(function(c){ if (c.bottom > Math.round(rr.bottom)) overflow = Math.max(overflow, c.bottom - Math.round(rr.bottom)); });
    out.push({row: i, rowBottom: Math.round(rr.bottom), rowTop: Math.round(rr.top),
              rulesBottom: rr2 ? Math.round(rr2.bottom) : null, chips: chips, overflow_px: overflow});
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
        for row in d:
            print(f"row{row['row']} top={row['rowTop']} bottom={row['rowBottom']} rulesBottom={row['rulesBottom']} overflow={row['overflow_px']}px")
            for c in row['chips']:
                print(f"   {c['cls']:<26} top={c['top']} bottom={c['bottom']} h={c['h']} '{c['text']}'")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
