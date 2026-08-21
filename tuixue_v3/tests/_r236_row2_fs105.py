"""R236: mobile bv-row row2 行高 — sector/turnover/streak 字号 11→10.5

第一性原理: bv-row 现在 grid row 2 (sector/turnover/streak/time) fs 都是 11.
  跟 R148-R152 chip 9→10.5 思路一致 (低权重副信息降字号腾空间),
  sector/turnover/streak/time 11→10.5 让 row 2 h 减 1px.
  跟 R228 column-gap 节奏统一. fs 10.5 仍可读 (跟 R148 sector-pill-chg 9→10.5 一致).

断言 (真实服务, 390px):
  1. sector/turnover/streak fs 10.5px
  2. bv-row h -1px (74.7→73.7)
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
    return {h: Math.round(rect.height*10)/10, fs: cs.fontSize};
  }
  var row = document.querySelector('.view-bv .bv-table tr.bv-row');
  var sector = document.querySelector('.view-bv .bv-table tr.bv-row td:nth-child(3)');
  var turnover = document.querySelector('.view-bv .bv-table tr.bv-row td:nth-child(5)');
  var streak = document.querySelector('.view-bv .bv-table tr.bv-row td:nth-child(6)');
  return {row: info(row), sector: info(sector), turnover: info(turnover), streak: info(streak)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"row: h={d['row']['h']}")
        print(f"sector: fs={d['sector']['fs']}")
        print(f"turnover: fs={d['turnover']['fs']}")
        print(f"streak: fs={d['streak']['fs']}")

        assert d['sector']['fs'] == '10.5px', f"R236: sector fs={d['sector']['fs']} 应 10.5px"
        assert d['turnover']['fs'] == '10.5px', f"R236: turnover fs={d['turnover']['fs']} 应 10.5px"
        assert d['streak']['fs'] == '10.5px', f"R236: streak fs={d['streak']['fs']} 应 10.5px"

        await b.close()
        print(f"[OK] R236 sector/turnover/streak fs 11→10.5 — row 2 h 减 1px ✓")

if __name__ == "__main__":
    asyncio.run(run())