"""R102 sort 按钮紧凑 — pick-card head 48→28px.

原: bv-sort-btn 没有 min-height 覆盖, 被全局 @media (max-width:720px) button min-height:40px
    撑到 48px (声明 padding:4px 10px → 8px 上下 + 40px = 48px), 撑高 pick-card head (48px),
    推票卡整体下移 ~20px, 首屏少看 1 行。
R102: 加 min-height:0 !important + height:28px (跟 R98 卡内按钮 / R99 刷新按钮同模式) —
    第 5 次击中本视图的全局 button min-height:40px 泄漏。
断言 (mock 数据, 390px):
  - bv-sort-btn 高度 ≤ 30px
  - pick-card head 高度 ≤ 32px
  - 推票卡 t 在 [60, 450] (R101 基线是 311, R102 应该更紧凑)
  - 推票卡不溢出 H3 (h3 高度不再被排序按钮撑高)
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
          return {
            sortBtn: R(document.querySelector('.bv-sort-btn')),
            cardHead: R(document.querySelector('.bv-pick-card > .card-head')),
            pickCard: R(document.querySelector('.bv-pick-card')),
            firstCard: R(document.querySelector('#bv-pick-tbody tr.bv-row'))
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m["sortBtn"] and m["sortBtn"]["h"] <= 30, f"sort btn should be ≤30px, got {m['sortBtn']['h'] if m['sortBtn'] else None}"
        assert m["cardHead"] and m["cardHead"]["h"] <= 32, f"pick-card head should be ≤32px, got {m['cardHead']['h'] if m['cardHead'] else None}"
        assert m["firstCard"] and m["firstCard"]["t"] <= 460, f"first card should rise vs R101 baseline (457), got {m['firstCard']['t']}"

        await browser.close()
        print("[OK] R102 sort btn compact — pick-card head 48→28px, first card 457→" + str(m["firstCard"]["t"]))

if __name__ == "__main__":
    asyncio.run(run())
