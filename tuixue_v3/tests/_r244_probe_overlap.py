"""R244 prep: 探针 code-link tap zone 是否与 sector 格重叠

第一性原理: R104/R229 给 code-link 32px 触控地板 + margin -8px 扩展热区,
  row 1 只有 26px 高 — 若热区溢出到 row 2 的 sector 格 (同列 x=12..84),
  点 sector 文本会误触股票跳转, 板块过滤被吞.
  本探针只量, 不改.
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
  var codeLink = row.querySelector('td:nth-child(1) a.code-link');
  var sector = row.querySelector('td:nth-child(3)');
  if (!codeLink || !sector) return {err: 'missing'};
  var cr = codeLink.getBoundingClientRect();
  var sr = sector.getBoundingClientRect();
  var rr = row.getBoundingClientRect();
  var overlap = Math.max(0, Math.min(cr.bottom, sr.bottom) - Math.max(cr.top, sr.top));
  return {
    row: {top: Math.round(rr.top), h: rr.height},
    codeLink: {top: Math.round(cr.top), bottom: Math.round(cr.bottom), h: Math.round(cr.height), w: Math.round(cr.width)},
    sector: {top: Math.round(sr.top), bottom: Math.round(sr.bottom), h: Math.round(sr.height), w: Math.round(sr.width)},
    overlap_px: Math.round(overlap * 10) / 10,
    sector_tag: sector.tagName,
    sector_html: sector.innerHTML.slice(0, 60)
  };
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        import json
        print(json.dumps(d, ensure_ascii=False, indent=1))
        if d and d.get('overlap_px', 0) > 0:
            print(f"[FIND] code-link 与 sector 重叠 {d['overlap_px']}px — 板块过滤热区被吞")
        else:
            print("[OK] 无重叠")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
