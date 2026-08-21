"""R247 prep: 量 change 格各文本变体渲染宽度 — 找 56px 内完整显示且保留精度的格式

第一性原理: '+10.03%' 7 字形在 13px/700/tabular 渲染 61px > 56px 盒 → 截断.
  候选:
    A. '+10.03'   (去 %, 6 字形) — % 在专属涨幅列是零信息噪声
    B. '10.03%'   (去 +, 6 字形) — 丢失涨跌方向 (红绿还在, 但色觉障碍者依赖符号)
    C. '+10.0%'   (1 位小数, 6 字形) — 丢精度
  同盒注入三种, 量实际宽度 + 是否溢出. 同时测 strong 行 (padding 6px 内容区更窄).
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
  var td = row.querySelector('td:nth-child(4)');
  var cls = td.className;
  var variants = ['+10.03%', '+10.03', '10.03%', '+10.0%', '+10.0', '-3.45%', '-3.45', '—'];
  var out = [];
  for (var i=0; i<variants.length; i++) {
    td.textContent = variants[i];
    var r = td.getBoundingClientRect();
    out.push({v: variants[i], clientW: Math.round(r.width), scrollW: td.scrollWidth,
              clip: td.scrollWidth > Math.round(r.width) + 1,
              paddingLeft: parseFloat(getComputedStyle(td).paddingLeft),
              paddingRight: parseFloat(getComputedStyle(td).paddingRight)});
  }
  return {cls: cls, out: out};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print("change td class:", d['cls'])
        for v in d['out']:
            pad = f" pad={v['paddingLeft']}+{v['paddingRight']}"
            print(f"  '{v['v']}': clientW={v['clientW']} scrollW={v['scrollW']} {'CLIP' if v['clip'] else 'ok'}{pad}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
