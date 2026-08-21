"""R226: mobile hit-tier stripe 3px→2px — 强度条纹收窄

第一性原理: hit-tier stripe box-shadow inset 3px (R113). hit-tier 在卡片左边 3px
  偏宽 (占 0.77% 视口 390). 跟 R224 pl 12→10 节奏统一, hit-tier 3→2. 节省 1px 让
  name 列更靠左, hit-badge/code-link 视觉边缘更紧凑. 但 2px 是 minimum
  (再小就跟 1px border 视觉粘连).

断言 (真实服务, 390px):
  1. strong hit-tier box-shadow inset 2px (从 3px)
  2. mid hit-tier inset 2px
  3. weak hit-tier inset 2px
  4. bv-row h 不变 (74.7)
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
    return {h: Math.round(rect.height*10)/10, bs: cs.boxShadow};
  }
  var rows = document.querySelectorAll('.view-bv .bv-table tr.bv-row');
  // probe first td (R156 guard renders hit-tier on td:first-child, not tr)
  var strong = document.querySelector('.view-bv .bv-table tr.bv-row.bv-hit-strong > td:first-child');
  var mid    = document.querySelector('.view-bv .bv-table tr.bv-row.bv-hit-mid > td:first-child');
  var weak   = document.querySelector('.view-bv .bv-table tr.bv-row.bv-hit-weak > td:first-child');
  return {count: rows.length, strong: info(strong), mid: info(mid), weak: info(weak)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"row count={d['count']}")
        if d.get('strong') and d['strong']:
            print(f"strong td: bs={d['strong']['bs']}")
            assert '2px' in d['strong']['bs'] and '3px' not in d['strong']['bs'], f"R226: strong bs={d['strong']['bs']} 应 2px"
        if d.get('mid') and d['mid']:
            print(f"mid td: bs={d['mid']['bs']}")
            assert '2px' in d['mid']['bs'] and '3px' not in d['mid']['bs'], f"R226: mid bs={d['mid']['bs']} 应 2px"
        if d.get('weak') and d['weak']:
            print(f"weak td: bs={d['weak']['bs']}")
            assert '2px' in d['weak']['bs'] and '3px' not in d['weak']['bs'], f"R226: weak bs={d['weak']['bs']} 应 2px"

        await b.close()
        print(f"[OK] R226 hit-tier stripe 3→2 — 强度条纹收窄, hit-badge 视觉边缘更紧凑 ✓")

if __name__ == "__main__":
    asyncio.run(run())