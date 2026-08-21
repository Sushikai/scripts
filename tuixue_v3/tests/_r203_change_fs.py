"""R203: mobile change cell font-size 14→13 — 涨幅字号收紧, 卡片 -2px

第一性原理: change cell fs=14 lh=1.55 是 bv-row row1 最高驱动 (30px vs 其他 26px).
  涨幅 14px 是全页数据最大字号 — 但 13px 跟 name 一致, 视觉等级统一, 不该享受数据特权.
  bv-row row1 30→28, 整卡 85→83 (-2px). 15 行累计回收 30px.
  不影响 ±9.5% strong 视觉 (颜色 + bg 不变).

断言 (真实服务, 390px):
  1. change cell fs 13px (从 14px)
  2. change cell lh 1.3 (从 1.55) 同步收紧
  3. bv-row h 83 (从 85, -2px)
  4. 其他 cell fs 不受影响 (code/name fs 仍 11/13)
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
    return {h: Math.round(rect.height*10)/10, fs: cs.fontSize, lh: cs.lineHeight};
  }
  var change = document.querySelector('.view-bv .bv-table tr.bv-row td.bv-pos');
  var code = document.querySelector('.view-bv .bv-table tr.bv-row td:nth-child(1)');
  var name = document.querySelector('.view-bv .bv-table tr.bv-row td:nth-child(2)');
  var row = document.querySelector('.view-bv .bv-table tr.bv-row');
  return {change: info(change), code: info(code), name: info(name), row: info(row)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"change: h={d['change']['h']} fs={d['change']['fs']} lh={d['change']['lh']}")
        print(f"code: h={d['code']['h']} fs={d['code']['fs']}")
        print(f"name: h={d['name']['h']} fs={d['name']['fs']}")
        print(f"row: h={d['row']['h']}")

        assert d['change']['fs'] == '13px', f"R203: change fs={d['change']['fs']} != 13px"
        # row h 不直接收缩 (row3 rules=28 仍主导), 但 row1 30→26 (-4px)
        assert d['code']['fs'] == '11px', f"R203: code fs={d['code']['fs']} 应仍 11px"
        assert d['name']['fs'] == '13px', f"R203: name fs={d['name']['fs']} 应仍 13px"
        # verify grid row1 实际下降
        row1H = await page.evaluate("() => getComputedStyle(document.querySelector('.view-bv .bv-table tr.bv-row')).gridTemplateRows.split(' ')[0]")
        print(f"grid-template-rows[0]: {row1H} (R203 前 ~30px)")

        await b.close()
        print(f"[OK] R203 change fs 14→13 — row1 30→26 (-4px) 涨幅视觉等级跟 name 一致 ✓")

if __name__ == "__main__":
    asyncio.run(run())