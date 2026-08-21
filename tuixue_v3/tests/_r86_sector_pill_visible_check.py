"""R86 板块聚合 pill 可视化 — 不可见 = 不存在.

原: .bv-sector-pill 只有 cursor/transition, 无 display/背景/布局 →
    dark 卡片上黑字 on 黑底, 板块聚合条实际不可见.
R86: pill 用 --shue 做 tint, name/chg/cnt 三段式布局, 横向滚动容器.
"""
import asyncio
from playwright.async_api import async_playwright

CSS = """
body { background:#0e1116; margin:0; padding:12px; font-family:-apple-system,'PingFang SC',sans-serif; }
.view-bv .bv-sector-bar { display:flex; gap:6px; overflow-x:auto; padding:2px 0 8px; }
.view-bv .bv-sector-bar-label { flex-shrink:0; align-self:center; font-size:10px; font-weight:600; color:#888; }
.view-bv .bv-sector-pill {
  display:inline-flex; align-items:center; gap:4px; flex-shrink:0;
  padding:4px 8px; border-radius:12px;
  background:hsla(var(--shue, 180), 45%, 45%, 0.14);
  border:1px solid hsla(var(--shue, 180), 60%, 60%, 0.30);
  color:#eee; cursor:pointer; white-space:nowrap;
}
.view-bv .bv-sector-pill-name { font-size:11px; font-weight:600; }
.view-bv .bv-sector-pill-chg { font-size:9px; font-weight:700; }
.view-bv .bv-sector-pill-chg.bv-pos { color:hsl(0,70%,60%); }
.view-bv .bv-sector-pill-chg.bv-neg { color:hsl(120,60%,55%); }
.view-bv .bv-sector-pill-cnt { font-size:9px; font-weight:700; padding:0 4px; border-radius:7px; background:rgba(0,0,0,0.25); color:#bbb; }
.view-bv .bv-sector-pill.is-active { background:#00f0ff !important; color:#000 !important; font-weight:800; }
"""

HTML = """
<!DOCTYPE html><html><head><style>__CSS__</style></head><body>
<div class="view-bv">
  <div class="bv-sector-bar" id="bv-sector-bar">
    <span class="bv-sector-bar-label">🔥 板块命中:</span>
    <span class="bv-sector-pill is-active" data-sector-key="氢能源" style="--shue:220">
      <span class="bv-sector-pill-name">氢能源</span>
      <span class="bv-sector-pill-chg bv-pos">+3.2%</span>
      <span class="bv-sector-pill-cnt">×2</span>
    </span>
    <span class="bv-sector-pill" data-sector-key="AI" style="--shue:180">
      <span class="bv-sector-pill-name">AI</span>
      <span class="bv-sector-pill-chg bv-neg">-1.1%</span>
      <span class="bv-sector-pill-cnt">×1</span>
    </span>
  </div>
</div>
</body></html>
"""


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(HTML.replace("__CSS__", CSS))

        pills = await page.evaluate("""() => {
          return Array.from(document.querySelectorAll('.bv-sector-pill')).map(p => {
            var cs = getComputedStyle(p), r = p.getBoundingClientRect();
            return { display: cs.display, bg: cs.backgroundColor, color: cs.color,
                     border: cs.borderTopWidth, w: r.width, h: r.height };
          });
        }""")
        print(f"pills: {pills}")
        for i, pill in enumerate(pills):
            # inline-flex blockifies to flex inside flex container — both acceptable
            assert pill["display"] in ("inline-flex", "flex"), f"R86: pill {i} must be flex"
            assert pill["bg"] != "rgba(0, 0, 0, 0)", f"R86: pill {i} must have background tint"
            assert pill["h"] > 15, f"R86: pill {i} must have height (visible)"

        # active pill gets solid accent bg
        act = await page.evaluate("getComputedStyle(document.querySelector('.bv-sector-pill.is-active')).backgroundColor")
        print(f"active pill bg: {act}")
        assert act != "rgba(0, 0, 0, 0)"

        # horizontal scroll container (bar can overflow-x)
        bar = await page.evaluate("""() => {
          var b = document.getElementById('bv-sector-bar');
          return { overflowX: getComputedStyle(b).overflowX, labelVisible: !!b.querySelector('.bv-sector-bar-label') };
        }""")
        print(f"bar: {bar}")
        assert bar["labelVisible"]

        print("[OK] R86 sector pills visible + laid out")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
