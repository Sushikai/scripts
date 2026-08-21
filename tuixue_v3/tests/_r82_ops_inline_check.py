"""R82 板块跳转并入操作行 — 同类动作同排, 不单独占整行撑高详情.

原 "🏷️ 板块" 独占一个 section (label+link ≈ 28px), 与"⚡ 操作"纵向堆叠.
R82: 板块链接并入 ops 行首格, 与 prev/jump/next 同级动作横排.
"""
import asyncio
from playwright.async_api import async_playwright

CSS = """
body { background:#0e1116; margin:0; padding:12px; font-family:-apple-system,'PingFang SC',sans-serif; }
.view-bv .bv-detail-inner { background:rgba(0,0,0,0.15); border-radius:0 0 8px 8px; padding:10px 12px; display:grid; grid-template-columns:1fr; gap:8px; }
.view-bv .bv-detail-section { display:flex; flex-direction:column; gap:4px; }
.view-bv .bv-detail-label { font-size:10px; color:#888; font-weight:600; }
.view-bv .bv-detail-ops { display:grid; grid-template-columns:1fr 1fr; gap:6px; }
.view-bv .bv-detail-sector-link.bv-detail-op {
  display:inline-block; align-self:flex-start; margin-top:4px; padding:6px 12px; border-radius:4px;
  background:rgba(0,240,255,0.10); color:#7dd3fc; border:1px solid rgba(0,240,255,0.35);
  font-size:12px; font-weight:600; text-decoration:none;
}
.btn-mini { background:rgba(255,255,255,0.08); color:#ddd; border:1px solid #333; padding:6px 12px; border-radius:4px; font-size:12px; }
"""

HTML = """
<!DOCTYPE html><html><head><style>__CSS__</style></head><body>
<div class="view-bv"><div class="bv-detail-inner">
  <div class="bv-detail-section"><span class="bv-detail-label">💬 UP 主原话</span><div class="bv-detail-quote">弱转强要等放量确认</div></div>
  <div class="bv-detail-section"><span class="bv-detail-label">⚡ 操作</span><div class="bv-detail-ops">
    <a class="bv-detail-sector-link bv-detail-op" data-goto-sector="氢能源" href="javascript:void(0)">🏷️ 氢能源</a>
    <button class="btn-mini bv-detail-jump" data-goto-stock="600123">查看个股页 →</button>
    <button class="btn-mini bv-detail-prev" data-bv-nav="-1">← 上一只</button>
    <button class="btn-mini bv-detail-next" data-bv-nav="1">下一只 →</button>
  </div></div>
</div></div>
</body></html>
"""


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(HTML.replace("__CSS__", CSS))

        # sector link styled as op button (inline-block, not a stacked label section)
        st = await page.evaluate("""() => {
          var el = document.querySelector('.bv-detail-sector-link');
          var cs = getComputedStyle(el);
          return { display: cs.display, bg: cs.backgroundColor,
                   rect: el.getBoundingClientRect().height };
        }""")
        print(f"sector op: {st}")
        # flex item: display blockified, but shares ops row with buttons
        assert st["display"] in ("inline-block", "block"), "R82: sector link is flex item"
        assert st["rect"] > 0

        # ops 2×2 grid: row1 = [板块, 个股页] (去别处), row2 = [上一只, 下一只] (扫列表)
        tops = await page.evaluate("""() => {
          return Array.from(document.querySelectorAll('.bv-detail-ops > *')).map(e => e.getBoundingClientRect().top);
        }""")
        print(f"ops tops: {tops}")
        assert len(tops) == 4
        # row1 (top=min) has exactly 2 items, row2 (top=max) has 2 items → 2×2 not ragged wrap
        # 用 8px 容差 (按钮内 line-height 差异会产生 ±4px 顶差)
        r1 = [t for t in tops if abs(t - min(tops)) < 8]
        r2 = [t for t in tops if abs(t - max(tops)) < 8]
        assert len(r1) == 2 and len(r2) == 2, f"R82: expected 2×2 grid, got row1={r1} row2={r2}"

        # sector click still delegated (closest) → the link's data attribute intact
        sec = await page.evaluate("document.querySelector('.bv-detail-sector-link').getAttribute('data-goto-sector')")
        print(f"sector attr: {sec}")
        assert sec == "氢能源"

        # detail height reduced vs old stacked (label + link line was ~28px; now inline)
        h = await page.evaluate("document.querySelector('.bv-detail-inner').getBoundingClientRect().height")
        print(f"detail inner height: {h}px")
        assert h < 250, "R82: detail should be compact (sector section removed)"

        print("[OK] R82 sector link inlined into ops row")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
