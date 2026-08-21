"""R36 phase color coding."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    #bv-pick-count .bv-phase-pre     { color: #f7c948; }
    #bv-pick-count .bv-phase-early   { color: #4ade80; }
    #bv-pick-count .bv-phase-midday  { color: #94a3b8; }
    #bv-pick-count .bv-phase-late    { color: #f87171; }
    #bv-pick-count .bv-phase-closing { color: #c084fc; }
    #bv-pick-count .bv-phase-close   { color: #64748b; }
    """
    js = """
    var _phase = 'early';
    var _dataTs = 0;
    var _picks = [{code:'X'}];
    function renderPicks() {
      var count = document.querySelector('#bv-pick-count');
      var _phaseMap = {
        pre_market:     { label: '🟡集合', cls: 'bv-phase-pre' },
        early:          { label: '🟢早盘', cls: 'bv-phase-early' },
        midday:         { label: '🟡午休', cls: 'bv-phase-midday' },
        late_afternoon: { label: '🔴尾盘', cls: 'bv-phase-late' },
        closing:        { label: '🟣收盘', cls: 'bv-phase-closing' },
        close:          { label: '⚫盘后', cls: 'bv-phase-close' }
      };
      var _phaseInfo = _phaseMap[_phase] || _phaseMap.close;
      var _tsStr = ' · <span class="' + _phaseInfo.cls + '">' + _phaseInfo.label + '</span>';
      count.innerHTML = '(扫描 ≥1 / 命中 1' + _tsStr + ')';
    }
    window.__setPhase = function(p) { _phase = p; renderPicks(); };
    """
    html = f"""<!DOCTYPE html><html><head><style>{css}</style></head>
<body>
<div id="bv-pick-count">(加载中)</div>
<script>{js}</script>
</body></html>"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        await page.set_content(html)

        tests = [
            ('early',          'bv-phase-early',   '🟢早盘', 'rgb(74, 222, 128)'),
            ('late_afternoon', 'bv-phase-late',    '🔴尾盘', 'rgb(248, 113, 113)'),
            ('midday',         'bv-phase-midday',  '🟡午休', 'rgb(148, 163, 184)'),
            ('closing',        'bv-phase-closing', '🟣收盘', 'rgb(192, 132, 252)'),
            ('close',          'bv-phase-close',   '⚫盘后', 'rgb(100, 116, 139)'),
            ('pre_market',     'bv-phase-pre',     '🟡集合', 'rgb(247, 201, 72)'),
        ]
        all_ok = True
        for phase, cls, label, expectColor in tests:
            await page.evaluate(f"window.__setPhase('{phase}')")
            span_cls = await page.evaluate("document.querySelector('#bv-pick-count span').className")
            span_text = await page.text_content("#bv-pick-count span")
            color = await page.evaluate("getComputedStyle(document.querySelector('#bv-pick-count span')).color")
            # normalize whitespace
            color_normalized = ' '.join(color.split())
            ok = (span_cls == cls) and (label in span_text) and (color_normalized == expectColor)
            mark = "✓" if ok else "✗"
            print(f"{mark} phase={phase} cls={span_cls} text={span_text!r} color={color_normalized}")
            if not ok:
                all_ok = False
        assert all_ok
        print("[OK] R36 phase color coding works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())