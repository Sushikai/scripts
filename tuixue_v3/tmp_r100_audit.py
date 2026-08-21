"""R100 audit: full first-screen budget — everything above + inside the fold (390×844)."""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 } } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'昨日分歧今日修复', score_weight:10, conditions:[], quote:'...', timestamp:'00:01' },
    { id:'BV02', title:'低位首板', category:'首板', description:'低位首板试错', score_weight:8, conditions:[], quote:'...', timestamp:'00:02' },
    { id:'BV03', title:'龙头战法', category:'龙头', description:'卡位真龙', score_weight:7, conditions:[], quote:'...', timestamp:'00:03' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01','BV02'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0 },
    { code:'000001', name:'平安银行', streak:2, matched_rules:['BV01'], score:76,
      change_pct:3.2, amount_yi:45.2, volume_ratio:1.8, turnover_pct:5.2, seal_ratio:20,
      sector:'银行', first_time:'10:05', phase:'close', burst_count:2 },
    { code:'000002', name:'万科A', streak:1, matched_rules:['BV03'], score:70,
      change_pct:1.1, amount_yi:22.0, volume_ratio:1.2, turnover_pct:3.1, seal_ratio:5,
      sector:'房地产', first_time:'11:20', phase:'close', burst_count:0 }
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

        m = await page.evaluate("""() => {
          var vh = window.innerHeight;
          var R = function(el){ if(!el) return null; var r=el.getBoundingClientRect();
            return { t: Math.round(r.top), b: Math.round(r.bottom), h: Math.round(r.height), w: Math.round(r.width) } };
          var out = { vh: vh };
          // walk direct children of .view-bv and the pick-card internals
          var view = document.querySelector('.view-bv');
          if (view) {
            out.stack = [];
            for (var i=0; i<view.children.length; i++) {
              var ch = view.children[i];
              var r = ch.getBoundingClientRect();
              if (r.bottom < 0 || r.top > vh) continue; // only above/inside fold
              out.stack.push({
                tag: ch.tagName, cls: (ch.className||'').toString().slice(0,50),
                R: { t: Math.round(r.top), b: Math.round(r.bottom), h: Math.round(r.height) }
              });
            }
          }
          var firstCard = document.querySelector('#bv-pick-tbody tr.bv-row');
          out.firstCard = R(firstCard);
          // what's between view-head bottom and first card top?
          var head = document.querySelector('.view-bv .view-head');
          out.gapAfterHead = head ? { headBottom: Math.round(head.getBoundingClientRect().bottom), firstCardTop: out.firstCard ? out.firstCard.t : null } : null;
          // card internals of first card
          if (firstCard) {
            var tds = [];
            firstCard.querySelectorAll('td').forEach(function(td){
              if (getComputedStyle(td).display === 'none') return;
              var r = td.getBoundingClientRect();
              tds.push({ nth: td.cellIndex, cls: (td.className||'').toString().slice(0,30), h: Math.round(r.height), w: Math.round(r.width) });
            });
            out.card = { rowH: Math.round(firstCard.getBoundingClientRect().height), tds: tds };
          }
          return out;
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))
        await browser.close()

asyncio.run(run())
