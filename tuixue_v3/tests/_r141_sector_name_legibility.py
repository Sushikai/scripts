"""R141 mobile .bv-sector-name span font 10→11px — R135 td 子元素 typography 收尾.

原: R135 把 .bv-table td:nth-child(3) sector 10→11px (cell 主字号),
    但 td 内 .bv-sector-name 子 span 显式 font 10px, 子元素浮空。
R141: .bv-sector-name span 10→11px (跟 R135 td font 体系一致)。
第一性原理: 10px 在 mobile 凑近看不清, R135 已修 td font, 子 span 不修会让 td 内主文字 11px + 额外 span 10px 视觉错位。
    typography 体系要求 cell 内所有可见文字字号一致 (除明确 badge/button 元素)。
R135 守护: td:nth-child(3) sector 11px 不动。
R123/R131/R132 守护: chip/badge/motto 自身显式字号不动。
断言 (mock 数据, 390px):
  - sector-name span font = 11px (was 10)
  - color 仍 ink-2 rgb(51,65,85) (R135 同步)
  - td sector 仍 11px (R135 守护)
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 } } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'...', score_weight:10, conditions:[], quote:'...', timestamp:'00:01' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:0.65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0,
      top_rule: { id:'BV01', title:'弱转强', quote:'弱转强核心是昨日分歧今日修复', timestamp:'00:35', score_weight:10, weight:10, value:25 } }
  ], phase:'close', ts: Date.now()/1000 } },
  '/api/bv/backtest': { ok:true, data: { trades: 120, win_rate_pct: 62, avg_return_pct: 1.8, max_drawdown_pct: -12 } }
};
window._mockFetch = window.fetch;
window.fetch = function(url, opts){
  var u = String(url);
  for (var k in MOCK_RESPONSES) {
    if (u.indexOf(k) === 0) {
      return Promise.resolve({ ok:true, json: function(){ return Promise.resolve(MOCK_RESPONSES[k]); } });
    }
  }
  return window._mockFetch(url, opts);
};
"""

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844})
        await ctx.add_init_script(MOCK)
        page = await ctx.new_page()
        for attempt in range(5):
            try:
                await page.goto("http://127.0.0.1:7799/#bv", wait_until="networkidle", timeout=20000)
                break
            except Exception:
                await page.wait_for_timeout(2000)
        for i in range(15):
            await page.wait_for_timeout(800)
            if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 1:
                break
        await page.wait_for_timeout(500)

        # Inject bv-sector-name span into td sector (testing CSS class isolation)
        m = await page.evaluate(r"""() => {
          var row = document.querySelector('#bv-pick-tbody tr.bv-row');
          if (!row) return {none: true};
          var sectorTd = row.querySelectorAll('td')[2];
          if (!sectorTd) return {none: true};
          var span = document.createElement('span');
          span.className = 'bv-sector-name';
          span.textContent = '白酒';
          sectorTd.appendChild(span);
          void document.body.offsetHeight;
          var tdCs = getComputedStyle(sectorTd);
          var spanCs = getComputedStyle(span);
          return {
            sectorTd: {fontSize: tdCs.fontSize, color: tdCs.color},
            sectorSpan: {fontSize: spanCs.fontSize, color: spanCs.color}
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("sectorSpan", {}).get("fontSize") == "11px", f"sector-name should be 11px (was 10), got {m.get('sectorSpan', {}).get('fontSize')}"
        # R135 守护 td sector 11px
        assert m.get("sectorTd", {}).get("fontSize") == "11px", f"td sector regression: {m.get('sectorTd', {}).get('fontSize')} (R135 must stay 11)"

        await browser.close()
        print(f"[OK] R141 sector-name span — 11px (was 10) | td sector 11px (R135 ✓)")

if __name__ == "__main__":
    asyncio.run(run())