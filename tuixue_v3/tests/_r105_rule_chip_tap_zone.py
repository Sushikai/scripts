"""R105 规则 chip 触控热区扩展 — 20px 高 → 32px 高 (Apple HIG).

原: audit 实测 .bv-rule-chip 41×20px / +N fold 26×20px, 拇指无法准确命中。
    用户想点 chip 过滤规则 (R48), 但视觉上和物理上都极小。
R105: padding-top/bottom 1→6 + 负 margin -6 让 chip 视觉尺寸不变, tap zone 撑高到 32px。
    横向也加 -2/+4 让密集排列下不串扰相邻 chip。
断言 (mock 数据, 390px):
  - .bv-rule-chip tap zone 高度 ≥ 32px
  - chip 视觉字号不变 (10px, 不撑大卡片布局)
  - 卡片 row height 不显著变化
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
    { id:'BV02', title:'低位首板', category:'首板', description:'低位首板试错', score_weight:8, conditions:[], quote:'...', timestamp:'00:02' },
    { id:'BV03', title:'卡位', category:'龙头', description:'真龙卡位', score_weight:7, conditions:[], quote:'...', timestamp:'00:03' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01','BV02','BV03','BV04','BV05'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:65,
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
          var chips = document.querySelectorAll('.view-bv .bv-rules-cell .chip');
          var fold = document.querySelector('.view-bv .bv-rules-cell .bv-rule-fold');
          var row = document.querySelector('#bv-pick-tbody tr.bv-row');
          var out = { chips: [], rowH: row ? Math.round(row.getBoundingClientRect().height) : null };
          chips.forEach(function(c){
            var r = c.getBoundingClientRect();
            var cs = getComputedStyle(c);
            out.chips.push({
              cls: c.className.slice(-30),
              w: Math.round(r.width),
              h: Math.round(r.height),
              fontSize: cs.fontSize,
              ruleId: c.getAttribute('data-rule-id') || c.textContent.trim().slice(0,5)
            });
          });
          return out;
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert len(m["chips"]) >= 3, f"need ≥3 chips, got {len(m['chips'])}"
        # all chips should have h >= 32 (Apple HIG min)
        for c in m["chips"]:
            assert c["h"] >= 32, f"chip {c['ruleId']} tap zone too small: {c['h']}px"
        # font size still 10px (visual unchanged)
        assert m["chips"][0]["fontSize"] == "10px", f"font should stay 10px, got {m['chips'][0]['fontSize']}"
        # row height should be reasonable (negative margin keeps layout stable)
        assert m["rowH"] is not None and 100 <= m["rowH"] <= 160, f"row height should stay reasonable, got {m['rowH']}"

        await browser.close()
        print(f"[OK] R105 rule chip tap zone — all chips {m['chips'][0]['h']}px tall")

if __name__ == "__main__":
    asyncio.run(run())
