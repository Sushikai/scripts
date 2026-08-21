"""R245 prep: 探针 col5 (btn) 组成 — 3 个按钮的真实宽度

第一性原理: col2 (name/turnover) 只有 73px, 但 col5 (btn) 吃 74px.
  若按钮能压缩, 释放空间给 col2, turnover 三信号单行就能放下,
  不用 2 行 (flex-wrap 让 row h 75→102 太贵). 本探针量每个按钮宽.
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
  var btnTd = row.querySelector('td.bv-jump-btn-cell');
  if (!btnTd) return {err: 'no btn td'};
  var tr = btnTd.getBoundingClientRect();
  var buttons = [];
  btnTd.querySelectorAll('button').forEach(function(btn){
    var r = btn.getBoundingClientRect();
    var cs = getComputedStyle(btn);
    buttons.push({cls: (btn.className||'').slice(0,20), text: (btn.textContent||'').trim(),
                  w: Math.round(r.width), h: Math.round(r.height), left: Math.round(r.left - tr.left),
                  padding: cs.paddingLeft + '/' + cs.paddingRight, fs: cs.fontSize, minW: cs.minWidth});
  });
  return {btnTdW: Math.round(tr.width), buttons: buttons};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"btnTdW={d['btnTdW']}")
        for b in d['buttons']:
            print(f"   {b['cls']:<20} '{b['text']}' w={b['w']} h={b['h']} left={b['left']} pad={b['padding']} minW={b['minW']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
