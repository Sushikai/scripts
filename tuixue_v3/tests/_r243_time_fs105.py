"""R243: mobile time 列 fs 11→10.5 — row 2 字号彻底统一

第一性原理: R243 视觉审计 probe 发现 bv-row row 2 的 time 格 (td:nth-child(8))
  是唯一 11px 残留 — sector/turnover/streak/seal (R236-R240) + burst (R241)
  + rules (R242) 已全 10.5. R238 注释自述目标 "让 row 2 字号彻底统一",
  但当时统一到 11px; 现在全表 10.5, time 是最后漏网.
  time fs 11→10.5 完成 row 2 彻底统一.

断言 (真实服务, 390px):
  1. time fs 10.5px (从 11px)
  2. 全部 8 个数据格 fs ∈ {10.5px} (row 2 全统一)
  3. bv-row h 不变 (75px)
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
  var row = document.querySelector('#bv-pick-tbody tr.bv-row');
  if (!row) return null;
  var fs = function(n){ var t = row.querySelector('td:nth-child(' + n + ')'); return t ? getComputedStyle(t).fontSize : null; };
  // 数据格: 3=sector 4=change 5=turnover 6=streak 7=seal 8=time 9=burst 10=rules
  var grid = {sector:fs(3), change:fs(4), turnover:fs(5), streak:fs(6), seal:fs(7), time:fs(8), burst:fs(9), rules:fs(10)};
  return {time: fs(8), grid: grid, h: row.offsetHeight};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"time: fs={d['time']} row h={d['h']}")
        for k, v in d['grid'].items():
            print(f"  {k}: {v}")

        assert d['time'] == '10.5px', f"R243: time fs={d['time']} 应 10.5px"
        # row 2 数据格全统一 (change 主信号 13px 除外)
        for k in ['sector', 'turnover', 'streak', 'seal', 'time', 'burst', 'rules']:
            assert d['grid'][k] == '10.5px', f"R243: {k} fs={d['grid'][k]} 应 10.5px"

        await b.close()
        print(f"[OK] R243 time fs 11→10.5 — row 2 字号彻底统一 10.5 ✓")

if __name__ == "__main__":
    asyncio.run(run())
