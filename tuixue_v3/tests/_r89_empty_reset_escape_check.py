"""R89 空态 reset 出口必须醒目可点 — 逃离空态的出口要明确可见.

原: 过滤后空态 (R50) 的"重置过滤/清除规则"按钮只有零散 inline style,
    无 .bv-reset-filter/.bv-reset-rule CSS, 8px 垂直 padding → 390px 手机上
    触控目标不足 44px, 用户困在空态难出来.
R89: .bv-empty 内 reset/retry 按钮全宽 + min-height:44px + accent 底色.
"""
import asyncio
from playwright.async_api import async_playwright

CSS = """
body { background:#0e1116; margin:0; font-family:-apple-system,'PingFang SC',sans-serif; }
.bv-empty { padding:2.5rem 1rem; }
.bv-empty .bv-reset-filter,
.bv-empty .bv-reset-rule,
.bv-empty .bv-retry-btn {
  display:block; width:100%; max-width:260px; margin:14px auto 0;
  padding:12px 20px; border-radius:8px; font-size:13px; font-weight:700;
  min-height:44px; cursor:pointer;
}
.bv-empty .bv-reset-filter,
.bv-empty .bv-reset-rule { background:var(--accent,#00e0ff); color:#000; border:0; }
.bv-empty .bv-reset-filter:active,
.bv-empty .bv-reset-rule:active { transform:scale(.97); }
.bv-empty .bv-retry-btn { background:#222; color:#eee; border:1px solid #444; }
"""

HTML = """
<!DOCTYPE html><html><head><style>__CSS__</style></head><body>
<div class="bv-empty">
  <div style="font-size:26px">🔍</div>
  <div style="font-size:14px">当前过滤条件下无命中</div>
  <div style="font-size:11px">全市场 50 只命中 · 但 板块「AI」筛掉全部</div>
  <button class="bv-reset-filter">↺ 重置过滤</button>
</div>
</body></html>
"""


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(HTML.replace("__CSS__", CSS))

        btn = await page.evaluate("""() => {
          var b = document.querySelector('.bv-reset-filter');
          var cs = getComputedStyle(b), r = b.getBoundingClientRect();
          return { h: r.height, w: r.width, display: cs.display,
                   minH: cs.minHeight, bg: cs.backgroundColor,
                   color: cs.color, radius: cs.borderRadius };
        }""")
        print(f"reset btn: {btn}")
        assert btn["h"] >= 44, f"R89: tap target must be >=44px, got {btn['h']}px"
        assert btn["minH"] == "44px", f"R89: min-height 44px, got {btn['minH']}"
        assert btn["display"] in ("block", "flex"), f"R89: block/flex, got {btn['display']}"
        # accent cyan bg + black text
        assert btn["bg"] == "rgb(0, 224, 255)", f"R89: accent bg, got {btn['bg']}"
        assert btn["color"] == "rgb(0, 0, 0)", f"R89: black text, got {btn['color']}"
        assert btn["radius"] == "8px", f"R89: rounded, got {btn['radius']}"

        # retry button variant also ≥44px
        await page.set_content(HTML.replace("__CSS__", CSS).replace(
            'class="bv-reset-filter"', 'class="bv-retry-btn"').replace("↺ 重置过滤", "🔄 重试"))
        rb = await page.evaluate("""() => {
          var b = document.querySelector('.bv-retry-btn');
          var cs = getComputedStyle(b), r = b.getBoundingClientRect();
          return { h: r.height, bg: cs.backgroundColor, color: cs.color };
        }""")
        print(f"retry btn: {rb}")
        assert rb["h"] >= 44, f"R89: retry tap target {rb['h']}px"
        assert rb["bg"] == "rgb(34, 34, 34)", f"R89: retry bg-3, got {rb['bg']}"

        print("[OK] R89 empty-state reset escape hatch ≥44px + accent")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
