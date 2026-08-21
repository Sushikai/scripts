"""R28 stale warning verification — offline Playwright test."""
import asyncio
import json
import time
from playwright.async_api import async_playwright


async def run():
    css = """
    .view-bv { display: block; padding: 12px; background: #0f0f12; color: #ddd; }
    .bv-card { background: #1a1a1f; border-radius: 8px; padding: 12px; margin-bottom: 12px; }
    .bv-card h3 { color: #00f0ff; margin: 0 0 8px 0; font-size: 14px; }
    #bv-pick-count { font-size: 12px; color: #aaa; padding: 4px 0; }
    .bv-pick-row { display: flex; gap: 8px; padding: 8px; border-bottom: 1px solid #2a2a2a; }
    .bv-pick-row .code { font-weight: 700; color: #fff; }
    .bv-pick-row .name { color: #aaa; font-size: 12px; }
    .bv-pick-row .chg { color: #ff6b6b; font-weight: 700; margin-left: auto; }
    #bv-pick-count.is-stale { color: #ffb547; font-weight: 600; }
    #bv-pick-count.is-very-stale { color: #ff6b6b; font-weight: 700; }
    """
    js = """
    window.__results = [];
    function $(s) { return document.querySelector(s); }
    var _dataTs = 0;
    var _picks = [];
    function renderPicks() {
      var count = $('#bv-pick-count');
      if (!count) return;
      var _tsStr = '';
      if (_dataTs > 0) {
        var _d = new Date(_dataTs * 1000);
        var _hh = String(_d.getHours()).padStart(2, '0');
        var _mm = String(_d.getMinutes()).padStart(2, '0');
        _tsStr = ' · 快照 ' + _hh + ':' + _mm;
        var _age = Math.floor((Date.now() / 1000) - _dataTs);
        if (_age > 60) {
          if (_age > 300) {
            _tsStr += ' ⚠️ 陈旧 ' + Math.floor(_age / 60) + '分钟';
          } else {
            _tsStr += ' ⚠️ ' + _age + 's';
          }
        }
      }
      count.textContent = '(扫描 ≥' + _picks.length + ' / 命中 ' + _picks.length + _tsStr + ')';
      if (_dataTs > 0) {
        var _ageNow = Math.floor((Date.now() / 1000) - _dataTs);
        count.classList.remove('is-stale', 'is-very-stale');
        if (_ageNow > 300) count.classList.add('is-very-stale');
        else if (_ageNow > 60) count.classList.add('is-stale');
      } else {
        count.classList.remove('is-stale', 'is-very-stale');
      }
    }
    window.__setAge = function(sec) {
      _dataTs = Math.floor(Date.now() / 1000) - sec;
      _picks = [
        {code: '600000', name: '浦发银行', chg: 9.95},
        {code: '000001', name: '平安银行', chg: 10.01}
      ];
      renderPicks();
    };
    """
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{css}</style></head>
<body>
<section class="view-bv">
  <div class="bv-card">
    <h3>实时推票</h3>
    <div id="bv-pick-count">(加载中)</div>
    <div id="bv-pick-tbody"></div>
  </div>
</section>
<script>{js}</script>
</body></html>"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        await page.set_content(html)

        # Case 1: 30s old — no warning
        await page.evaluate("window.__setAge(30)")
        c1 = await page.text_content("#bv-pick-count")
        cls1 = await page.get_attribute("#bv-pick-count", "class")
        print(f"[30s] text={c1!r} class={cls1!r}")
        assert "⚠️" not in c1, f"30s should NOT have warning, got {c1!r}"
        assert "is-stale" not in (cls1 or ""), f"30s should NOT have stale class, got {cls1!r}"

        # Case 2: 120s old — warning + is-stale
        await page.evaluate("window.__setAge(120)")
        c2 = await page.text_content("#bv-pick-count")
        cls2 = await page.get_attribute("#bv-pick-count", "class")
        print(f"[120s] text={c2!r} class={cls2!r}")
        assert "⚠️" in c2, f"120s should have warning, got {c2!r}"
        assert "120s" in c2, f"120s should show '120s', got {c2!r}"
        assert "is-stale" in (cls2 or ""), f"120s should have is-stale class, got {cls2!r}"
        assert "is-very-stale" not in (cls2 or ""), f"120s should NOT be very-stale"

        # Case 3: 600s (10min) old — very-stale
        await page.evaluate("window.__setAge(600)")
        c3 = await page.text_content("#bv-pick-count")
        cls3 = await page.get_attribute("#bv-pick-count", "class")
        print(f"[600s] text={c3!r} class={cls3!r}")
        assert "⚠️" in c3, f"600s should have warning"
        assert "陈旧" in c3, f"600s should show '陈旧 N分钟', got {c3!r}"
        assert "is-very-stale" in (cls3 or ""), f"600s should have is-very-stale class, got {cls3!r}"

        # Take a screenshot to visually confirm
        await page.evaluate("window.__setAge(120)")
        await page.screenshot(path="/tmp/bv_r28_test.png", full_page=True)
        print(f"[OK] all 3 cases pass — screenshot @ /tmp/bv_r28_test.png")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
