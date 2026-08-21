"""R245: 探针 motto-badge 在多少行出现 + name 格真实占用

第一性原理: motto 是"为什么推"标签 (reasons), 不是身份 (name).
  决定它应留在 name 格还是移到规则行 (row3), 需知道:
    1. 多少行有 motto (所有行 vs 仅 top-1/filter)
    2. name 文本宽 vs motto 宽 — 谁在挤谁
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
  var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
  var out = [];
  for (var i=0; i<Math.min(8, rows.length); i++) {
    var row = rows[i];
    var rr = row.getBoundingClientRect();
    var nameTd = row.querySelector('td:nth-child(2)');
    var nR = nameTd.getBoundingClientRect();
    var mb = nameTd.querySelector('.bv-motto-badge');
    var mbR = mb ? mb.getBoundingClientRect() : null;
    var rng = document.createRange();
    var firstText = '';
    if (nameTd.firstChild) { rng.selectNodeContents(nameTd); }
    // 提取 name 文本 (去掉 badge)
    var nameTxt = (nameTd.textContent||'').replace(mb ? mb.textContent : '', '').trim();
    out.push({
      i: i,
      name: nameTxt.slice(0, 8),
      nameTdW: Math.round(nR.width),
      scrollW: nameTd.scrollWidth,
      motto: mb ? mb.textContent.trim() : null,
      mottoW: mb ? Math.round(mbR.width) : null,
      mottoClipped: mb ? (mbR.right > nR.right + 0.5 || mbR.left < nR.left - 0.5) : null,
      mottoRight: mb ? Math.round(mbR.right - rr.left) : null,
      tdRight: Math.round(nR.right - rr.left)
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
        nWithMotto = sum(1 for r in d if r['motto'])
        print(f"rows={len(d)} withMotto={nWithMotto}")
        for r in d:
            mc = "CLIPPED" if r['mottoClipped'] else "ok"
            print(f"  r{r['i']}: name='{r['name']}' tdW={r['nameTdW']} scrollW={r['scrollW']}"
                  f" motto='{r['motto']}' mottoW={r['mottoW']} [{mc}] right={r['mottoRight']}/{r['tdRight']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
