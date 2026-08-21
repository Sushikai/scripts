"""R240: mobile streak fw 700→600 — 连板减重

第一性原理: streak 列 fw 700 + color accent + fs 10.5 (R236).
  视觉权重偏重 — 700 跟 change/seal (700) 视觉平齐,
  但 streak 是次要信号 (跟 row 2 其他列同质).
  跟 R235 pick-count fw 600 思路一致, streak fw 700→600 让 row 2 视觉权重更平衡.

断言 (真实服务, 390px):
  1. streak fw 600 (从 700)
  2. bv-row h 不变
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
    return {fw: cs.fontWeight, fs: cs.fontSize};
  }
  // 非首板行 (R15 first-board fw 800 覆盖)
  var rows = document.querySelectorAll('.view-bv .bv-table tr.bv-row');
  var streak = null;
  for (var i=0; i<rows.length; i++) {
    if (!rows[i].classList.contains('is-first-board')) {
      streak = rows[i].querySelector('td:nth-child(6)');
      if (streak) break;
    }
  }
  return {streak: info(streak)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"streak: fw={d['streak']['fw']} fs={d['streak']['fs']}")

        assert d['streak']['fw'] == '600', f"R240: streak fw={d['streak']['fw']} 应 600"
        # fs 不变 (R236 10.5)
        assert d['streak']['fs'] == '10.5px', f"R240: streak fs={d['streak']['fs']} 应仍 10.5px"

        await b.close()
        print(f"[OK] R240 streak fw 700→600 — 连板减重, row 2 视觉权重平衡 ✓")

if __name__ == "__main__":
    asyncio.run(run())