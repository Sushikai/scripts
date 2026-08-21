"""R106 audit — 找下一个 first-principle 优化目标.

列出所有 mobile (390px) 上的可见交互元素, 找出:
  1. tap zone < 32px (Apple HIG 违反)
  2. 视觉过紧但难命中
  3. 触控与视觉不一致
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 },
    philosophy: ['仓位管理是核心, 不重仓单只', '关注板块效应, 不孤军奋战', '严格止损, 单只亏损-5%必出', '跟随主线题材, 不参与边角'] } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'昨日分歧今日修复', score_weight:10, conditions:[], quote:'...', timestamp:'00:01' },
    { id:'BV02', title:'低位首板', category:'首板', description:'低位首板试错', score_weight:8, conditions:[], quote:'...', timestamp:'00:02' },
    { id:'BV03', title:'卡位', category:'龙头', description:'真龙卡位', score_weight:7, conditions:[], quote:'...', timestamp:'00:03' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01','BV02','BV03','BV04','BV05'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:0.65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0 },
    { code:'000001', name:'平安银行', streak:2, matched_rules:['BV01','BV02'], score:76,
      change_pct:3.2, amount_yi:45.2, volume_ratio:1.8, turnover_pct:5.2, seal_ratio:0.2,
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
        await page.goto("http://127.0.0.1:7799/#bv", wait_until="domcontentloaded", timeout=20000)
        for i in range(15):
            await page.wait_for_timeout(800)
            if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") > 0:
                break
        await page.wait_for_timeout(500)

        m = await page.evaluate(r"""() => {
          function inspect(sel, label, threshold) {
            var els = document.querySelectorAll(sel);
            var items = [];
            els.forEach(function(e){
              var r = e.getBoundingClientRect();
              if (r.width === 0) return;
              items.push({ label: label, txt: (e.textContent||'').trim().slice(0,12), w: Math.round(r.width), h: Math.round(r.height), below: Math.round(r.height) < threshold });
            });
            return items;
          }
          return {
            filterChip: inspect('.view-bv .bv-filter-chip', 'chip', 32),
            sortBtn: inspect('.view-bv .bv-sort-btn', 'sort', 32),
            bvBuy: inspect('.view-bv .bv-buy-window', 'buy', 32),
            bvCatSum: inspect('.view-bv .bv-cat-summary', 'cat-sum', 32),
            creedMore: inspect('.view-bv .bv-creed-more', 'creed-more', 32),
            badge: inspect('.view-bv .bv-pick-count', 'pick-count', 32),
            pvLink: inspect('.view-bv .chip[class*="bv-rule-chip"]', 'rule-chip', 32),
            selLink: inspect('.view-bv a.code-link', 'code-link', 32),
            action: inspect('.view-bv .bv-card-actions button, .view-bv .bv-card-actions a', 'card-action', 32)
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())