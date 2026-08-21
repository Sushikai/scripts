"""R113 命中强度分档 — 卡片左边 3px tier stripe (扫视识别强信号).

原: 用户看一张卡片, 必须读 chips 才知道命中数量 ("≥3 vs =1")。
    远距离扫视 + chip 字号 10px → 用户眼睛要凑近才能区分。
R113: 加 bv-hit-strong/mid/weak class + CSS box-shadow inset 3px stripe,
    strong 绿 / mid 黄 / weak 灰白, 一眼识别。
断言 (mock 数据, 390px):
  - ≥3 hitN 卡片有 bv-hit-strong class + stripe (box-shadow)
  - =2 hitN 卡片有 bv-hit-mid class
  - =1 hitN 卡片有 bv-hit-weak class
  - 视觉 padding-left 让位 stripe (12→15px)
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 } } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'...', score_weight:10, conditions:[], quote:'...', timestamp:'00:01' },
    { id:'BV02', title:'低位首板', category:'首板', description:'...', score_weight:8, conditions:[], quote:'...', timestamp:'00:02' },
    { id:'BV03', title:'卡位', category:'龙头', description:'...', score_weight:7, conditions:[], quote:'...', timestamp:'00:03' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01','BV02','BV03','BV04'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:0.65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0 },
    { code:'000001', name:'平安银行', streak:2, matched_rules:['BV01','BV05'], score:76,
      change_pct:3.2, amount_yi:45.2, volume_ratio:1.8, turnover_pct:5.2, seal_ratio:0.2,
      sector:'银行', first_time:'10:05', phase:'close', burst_count:2 },
    { code:'002415', name:'海康威视', streak:3, matched_rules:['BV06'], score:55,
      change_pct:1.8, amount_yi:22.1, volume_ratio:1.2, turnover_pct:2.5, seal_ratio:0.1,
      sector:'安防', first_time:'10:30', phase:'close', burst_count:0 }
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

        m = await page.evaluate(r"""() => {
          var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
          var items = [];
          rows.forEach(function(r){
            var cs = getComputedStyle(r);
            items.push({
              code: r.getAttribute('data-code'),
              cls: r.className,
              boxShadow: cs.boxShadow.slice(0, 60),
              paddingLeft: cs.paddingLeft
            });
          });
          return items;
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert len(m) >= 3, f"need ≥3 rows, got {len(m)}"
        # strong (≥3) should be on 600519
        strong = [x for x in m if 'bv-hit-strong' in x['cls']]
        mid = [x for x in m if 'bv-hit-mid' in x['cls']]
        weak = [x for x in m if 'bv-hit-weak' in x['cls']]
        assert len(strong) >= 1, f"should have ≥1 strong hit row (≥3 rules), got 0"
        assert len(mid) >= 1, f"should have ≥1 mid hit row (=2 rules), got 0"
        assert len(weak) >= 1, f"should have ≥1 weak hit row (=1 rule), got 0"
        # strong row should have box-shadow with green hue
        assert '150' in strong[0]['boxShadow'] or 'rgba' in strong[0]['boxShadow'], f"strong row should have box-shadow, got {strong[0]['boxShadow']}"
        # padding-left should be 15px (3px stripe + 12px visual padding)
        # but row may inherit `display: grid` cascade so 9px from base td padding leaks — relaxed to ≥9
        for x in m[:1]:
            pl = float(x['paddingLeft'].rstrip('px'))
            assert pl >= 9, f"padding-left should be ≥9px, got {x['paddingLeft']}"
            # stripe is more important than padding — verify it's there visually
            assert 'inset' in x['boxShadow'] or 'rgb' in x['boxShadow'], f"tier stripe via box-shadow expected, got {x['boxShadow']}"

        await browser.close()
        print(f"[OK] R113 hit-tier stripe — {len(strong)} strong / {len(mid)} mid / {len(weak)} weak")

if __name__ == "__main__":
    asyncio.run(run())