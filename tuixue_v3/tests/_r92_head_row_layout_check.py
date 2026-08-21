"""R92 view-head 横排 — 刷新按钮与标题同排, 不独占一行.

原: .view-actions 通用 margin-top:16px + flex-wrap → BV 刷新按钮掉到
    phase banner 之下独立一行 (top 184-244, 60px), 推票卡被进一步推低.
R92: BV mobile view-head 改 flex 横排, 按钮与标题同排, 回收 ~60px.
"""
import asyncio
from playwright.async_api import async_playwright

CSS = """
body { background:#0e1116; margin:0; font-family:-apple-system,'PingFang SC',sans-serif; }
.view-head { display:flex; align-items:flex-start; justify-content:space-between; gap:8px; margin-bottom:8px; }
.view-head > div:first-child { flex:1; min-width:0; }
.view-head .view-actions { margin-top:0; flex-shrink:0; display:flex; gap:8px; }
.view-head .view-actions .btn-refresh { white-space:nowrap; min-height:34px; padding:6px 12px; background:#222; color:#eee; border:1px solid #444; border-radius:6px; cursor:pointer; }
.bv-title { font-size:18px; font-weight:600; color:#eee; }
.bv-meta { font-size:12px; color:#888; margin-top:4px; }
.bv-phase-banner { margin-top:6px; padding:6px 8px; border-radius:6px; background:#1a1a1a; font-size:12px; color:#ccc; }
"""

HTML = """
<!DOCTYPE html><html><head><style>__CSS__</style></head><body>
<header class="view-head">
  <div>
    <div class="bv-title">游资战法 <span class="sub">Bryan 交易随笔</span></div>
    <div class="bv-meta">UP主: Bryan · 视频战法</div>
    <div class="bv-phase-banner">⚫ 盘后守候 <span>TTL 300s</span></div>
  </div>
  <div class="view-actions">
    <button class="btn-refresh" id="bv-refresh">刷新</button>
  </div>
</header>
<div class="pick-card" style="margin-top:8px">推票表</div>
</body></html>
"""


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        await page.set_content(HTML.replace("__CSS__", CSS))

        geo = await page.evaluate("""() => {
          var title = document.querySelector('.bv-title').getBoundingClientRect();
          var btn = document.querySelector('#bv-refresh').getBoundingClientRect();
          var pick = document.querySelector('.pick-card').getBoundingClientRect();
          return {
            titleTop: Math.round(title.top), btnTop: Math.round(btn.top),
            titleH: Math.round(title.height), btnH: Math.round(btn.height),
            beside: Math.abs(btn.top - title.top) < 10,   // 同一行
            pickTop: Math.round(pick.top),
            btnH34: btn.height >= 34
          };
        }""")
        print(f"geo: {geo}")
        assert geo["beside"], f"R92: refresh must be beside title (btnTop {geo['btnTop']} vs titleTop {geo['titleTop']})"
        assert geo["btnH34"], f"R92: refresh tap target {geo['btnH']}px < 34"
        # The old layout: actions at top ~184, phase banner ends at 168 → btn 16px+ lower than title. New: beside.
        print("[OK] R92 view-head horizontal layout — refresh beside title")

        # also verify: total head height compressed vs before (was 168px)
        headH = await page.evaluate("""() => {
          var h = document.querySelector('.view-head').getBoundingClientRect();
          return Math.round(h.height);
        }""")
        print(f"head height: {headH}px")
        assert headH < 168, f"R92: head should be compressed below 168px, got {headH}"
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
