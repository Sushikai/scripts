"""R27+R28+R29 count text rendering — phase + timestamp + age."""
import asyncio
import json
from playwright.async_api import async_playwright


async def run():
    css = """
    .view-bv { display: block; padding: 12px; background: #0f0f12; color: #ddd; }
    .bv-card { background: #1a1a1f; border-radius: 8px; padding: 12px; margin-bottom: 12px; }
    #bv-pick-count { font-size: 12px; color: #aaa; padding: 4px 0; }
    #bv-pick-count.is-stale { color: #ffb547; font-weight: 600; }
    #bv-pick-count.is-very-stale { color: #ff6b6b; font-weight: 700; }
    """
    js = """
    var _phase = 'early';
    var _dataTs = 0;
    var _picks = [];
    function renderPicks() {
      var count = document.querySelector('#bv-pick-count');
      if (!count) return;
      var _phaseMap = {
        pre_market: '🟡集合', early: '🟢早盘', midday: '🟡午休',
        late_afternoon: '🔴尾盘', closing: '🟣收盘', close: '⚫盘后'
      };
      var _tsStr = ' · ' + (_phaseMap[_phase] || '⚫盘后');
      if (_dataTs > 0) {
        var _d = new Date(_dataTs * 1000);
        var _hh = String(_d.getHours()).padStart(2, '0');
        var _mm = String(_d.getMinutes()).padStart(2, '0');
        _tsStr += ' 快照 ' + _hh + ':' + _mm;
        var _age = Math.floor((Date.now() / 1000) - _dataTs);
        if (_age > 60) {
          if (_age > 300) _tsStr += ' ⚠️ 陈旧 ' + Math.floor(_age / 60) + '分钟';
          else _tsStr += ' ⚠️ ' + _age + 's';
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
    window.__setCase = function(phase, age) {
      _phase = phase;
      _dataTs = age > 0 ? Math.floor(Date.now() / 1000) - age : 0;
      _picks = [{code: '600000', name: 'X', chg: 9.95}];
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
  </div>
</section>
<script>{js}</script>
</body></html>"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        await page.set_content(html)

        tests = [
            ('early', 30, '🟢早盘',     False, False),
            ('early', 120, '🟢早盘',    True,  False),
            ('midday', 350, '🟡午休',   False, True),
            ('late_afternoon', 700, '🔴尾盘', False, True),
            ('close', 0, '⚫盘后',     False, False),
        ]
        all_ok = True
        for phase, age, expectPhase, expectStale, expectVeryStale in tests:
            await page.evaluate(f"window.__setCase('{phase}', {age})")
            txt = await page.text_content("#bv-pick-count")
            cls = await page.get_attribute("#bv-pick-count", "class") or ""
            ok_phase = expectPhase in txt
            ok_stale = ('is-stale' in cls) == expectStale
            ok_very  = ('is-very-stale' in cls) == expectVeryStale
            mark = "✓" if (ok_phase and ok_stale and ok_very) else "✗"
            print(f"{mark} phase={phase} age={age}s → text={txt!r} class={cls!r}")
            if not (ok_phase and ok_stale and ok_very):
                all_ok = False
        assert all_ok, "at least one case failed"
        print("[OK] R29 all phase+age combinations render correctly")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
