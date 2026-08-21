"""R103 filter-bar 紧凑 — 8/10px padding → 4/4px.

原: filter-bar padding 8+10=18px (单行 chip 浪费 10px), bar 总高 48px, 推票卡整体下移 ~10px。
R103: padding 8/10 → 4/4 (单行 30px chip 只需 4+4 微间距), bar 48→38px, 推票卡提前 10px。
断言 (mock 数据, 390px):
  - filter-bar 高度 ≤ 40px (基线 48, R103 目标 38)
  - filter chip 仍然全部 30px (R96 不退化)
  - 推票卡 t < R102 基线 (408), 应再早 8px+
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 } } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'昨日分歧今日修复', score_weight:10, conditions:[], quote:'...', timestamp:'00:01' },
    { id:'BV02', title:'低位首板', category:'首板', description:'低位首板试错', score_weight:8, conditions:[], quote:'...', timestamp:'00:02' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01','BV02'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0 },
    { code:'000001', name:'平安银行', streak:2, matched_rules:['BV01'], score:76,
      change_pct:3.2, amount_yi:45.2, volume_ratio:1.8, turnover_pct:5.2, seal_ratio:20,
      sector:'银行', first_time:'10:05', phase:'close', burst_count:2 }
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
            if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") > 0:
                break
        await page.wait_for_timeout(500)

        m = await page.evaluate("""() => {
          var R = function(el){ if(!el) return null; var r=el.getBoundingClientRect();
            return { t: Math.round(r.top), b: Math.round(r.bottom), h: Math.round(r.height) } };
          var chips = Array.from(document.querySelectorAll('.bv-filter-chip')).map(function(c){
            var r = c.getBoundingClientRect();
            return { h: Math.round(r.height) };
          });
          return {
            bar: R(document.querySelector('.bv-filter-bar')),
            chips: chips,
            firstCard: R(document.querySelector('#bv-pick-tbody tr.bv-row'))
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m["bar"] and m["bar"]["h"] <= 40, f"filter-bar should be ≤40px, got {m['bar']['h'] if m['bar'] else None}"
        assert m["chips"], "no filter chips"
        assert all(28 <= c["h"] <= 32 for c in m["chips"]), f"chips should stay 30px, got {m['chips']}"
        assert m["firstCard"] and m["firstCard"]["t"] <= 400, f"first card should rise vs R102 (408), got {m['firstCard']['t']}"

        await browser.close()
        print(f"[OK] R103 filter-bar compact — {m['bar']['h']}px, firstCard {m['firstCard']['t']}px")

if __name__ == "__main__":
    asyncio.run(run())
