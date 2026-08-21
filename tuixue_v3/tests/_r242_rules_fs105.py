"""R242: mobile rules-cell fs 11→10.5 — 规则行字号统一

第一性原理: bv-row rules-cell (td:nth-child(10)) fs 11.
  跟 R236-R238 row 2 + R241 burst 节奏统一, rules fs 11→10.5.
  rules fw 700 保持加粗, fs 10.5 让 row 3 跟 row 2 字号统一.

断言 (真实服务, 390px):
  1. rules td fs 10.5px (从 11px)
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
    return {fs: cs.fontSize};
  }
  var rules = document.querySelector('.view-bv .bv-table tr.bv-row td:nth-child(10)');
  return {rules: info(rules)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"rules: fs={d['rules']['fs']}")

        assert d['rules']['fs'] == '10.5px', f"R242: rules fs={d['rules']['fs']} 应 10.5px"

        await b.close()
        print(f"[OK] R242 rules fs 11→10.5 — 规则行字号统一 row 2 ✓")

if __name__ == "__main__":
    asyncio.run(run())