"""R108 audit — actual selectors on bv-page."""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 },
    philosophy: ['仓位管理是核心, 不重仓单只', '关注板块效应', '严格止损'] } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'昨日分歧今日修复', score_weight:10, conditions:[], quote:'...', timestamp:'00:01' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01','BV02','BV03'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:0.65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0 }
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
        await page.goto("http://127.0.0.1:7799/#bv", wait_until="domcontentloaded", timeout=20000)
        for i in range(15):
            await page.wait_for_timeout(800)
            if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") > 0:
                break
        await page.wait_for_timeout(500)
        await page.evaluate("() => { var top = document.querySelector('#bv-pick-tbody tr.bv-row.is-top'); if (top) top.click(); }")
        await page.wait_for_timeout(500)

        m = await page.evaluate(r"""() => {
          function inspect(sel, label) {
            var els = document.querySelectorAll(sel);
            var items = [];
            els.forEach(function(e){
              var r = e.getBoundingClientRect();
              if (r.width === 0) return;
              var cs = getComputedStyle(e);
              items.push({ label: label, txt: (e.textContent||'').trim().slice(0,15), w: Math.round(r.width), h: Math.round(r.height), fontSize: cs.fontSize });
            });
            return items;
          }
          return {
            catEyebrow: inspect('#bv-category-summary', 'cat-eyebrow'),
            catGrid: inspect('.bv-category-grid > *', 'cat-grid-item'),
            catSum: inspect('.bv-category-summary-card .card-eyebrow', 'cat-summary-eyebrow'),
            catTitle: inspect('.bv-category-grid h4', 'cat-grid-title'),
            metaRow: inspect('#bv-meta', 'meta-row'),
            cardEyebrow: inspect('.bv-card .card-eyebrow, .card .card-eyebrow', 'card-eyebrow')
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())