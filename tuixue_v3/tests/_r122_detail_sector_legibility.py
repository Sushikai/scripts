"""R122 bv-detail-sector-link 详情内 sector 链接字号 — 12px (R115 ops 体系延续).

原: R82 把 sector 跳转并入 .bv-detail-ops (跟 prev/jump/next 同级动作),
    字号走 .bv-detail-op 12px 标准。
R122: 不实际改字号, 但显式给 .bv-detail-sector-link 写 font-size: 12px (文档化),
    防止将来被其他 .bv-detail-sector 样式 (已删, 旧版) 误继承 11px。
    跟 R108/R119/R120/R121 typography 体系保持意识清晰: ops 按钮是 12px 标准。
断言 (mock 数据, 390px):
  - 展开 detail 后 .bv-detail-sector-link 字号 = 12px (跟其他 ops 按钮一致)
  - 跟 R115 detail-ops 12px 体系对齐
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
      top_rule: { id:'BV01', title:'弱转强', quote:'弱转强核心是昨日分歧今日修复', timestamp:'00:35', score_weight:10, weight:10, value:25 } },
    { code:'000001', name:'平安银行', streak:2, matched_rules:['BV01'], score:76,
      change_pct:3.2, amount_yi:45.2, volume_ratio:1.8, turnover_pct:5.2, seal_ratio:0.2,
      sector:'银行', first_time:'10:05', phase:'close', burst_count:2,
      top_rule: { id:'BV01', title:'弱转强', quote:'...', timestamp:'01:12', score_weight:10, weight:10, value:20 } },
    { code:'002415', name:'海康威视', streak:3, matched_rules:['BV01','BV02'], score:65,
      change_pct:5.2, amount_yi:33.1, volume_ratio:1.5, turnover_pct:3.5, seal_ratio:0.4,
      sector:'安防', first_time:'10:30', phase:'close', burst_count:1,
      top_rule: { id:'BV02', title:'低位首板', quote:'...', timestamp:'02:08', score_weight:8, weight:8, value:18 } }
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
                await page.goto("http://127.0.0.1:7799/#bv", wait_until="domcontentloaded", timeout=20000)
                break
            except Exception:
                await page.wait_for_timeout(2000)
        for i in range(15):
            await page.wait_for_timeout(800)
            if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 3:
                break
        await page.wait_for_timeout(500)

        try:
            await page.click("#bv-pick-tbody tr.bv-row:not(.is-top)", timeout=2000)
            await page.wait_for_timeout(800)
        except Exception:
            await page.evaluate("""() => {
                var tbody = document.getElementById('bv-pick-tbody');
                var row = document.querySelector('#bv-pick-tbody tr.bv-row:not(.is-top)');
                if (tbody && row) {
                    var ev = new MouseEvent('click', {bubbles:true, cancelable:true});
                    Object.defineProperty(ev, 'target', {value: row, enumerable:true});
                    tbody.onclick(ev);
                }
            }""")
            await page.wait_for_timeout(800)

        m = await page.evaluate(r"""() => {
          var sec = document.querySelector('.view-bv .bv-detail-sector-link');
          var meta = document.querySelector('.view-bv .bv-meta');
          if (!sec) return {none:true};
          return {
            sectorFont: getComputedStyle(sec).fontSize,
            sectorText: sec.textContent.trim().slice(0, 15),
            metaFont: meta ? getComputedStyle(meta).fontSize : null
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("sectorFont") == "12px", f"detail-sector-link should be 12px (R115 ops 体系), got {m.get('sectorFont')}"
        assert m.get("metaFont") == "11.5px", f"meta should be 11.5px (R108), got {m.get('metaFont')}"

        await browser.close()
        print(f"[OK] R122 detail-sector typography — sector-link {m['sectorFont']} (R115 ops 12px) | meta {m['metaFont']} (R108 typography 11.5px)")

if __name__ == "__main__":
    asyncio.run(run())