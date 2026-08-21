"""R137 .bv-table td:nth-child(9) burst (炸板计数) 字号 10→11px — typography 体系同步.

原: burst cell (炸板次数) font-size 10px, 跟 R135 sector/turnover/seal 11px 不一致,
    typography 体系浮空一格。
R137: burst 10→11px (跟 typography 体系一致), color 保留 ink-3 (弱信号)。
    R97 守护: 有炸板时炸板胜出占用 burst 格, 无炸板时空格让位给分数 (score cell grid-area:burst)。
第一性原理: 10px 让元信息 (板块/换手/封单/炸板/命中规则) 凑近看不清,
    R135 已修三格 (sector/turnover/seal), R137 收尾 burst, typography 体系第 14 档完整。
    burst 是低频信号 (只有炸板次数 > 0 时显示数字, 其他时让位 score), 11px 跟体系一致即可,
    不必加粗 (R107 buy-window 是高频信号才 700)。
R135/R97 守护: score 11px ink-1 (R128) / sector/turnover/seal 11px ink-2 (R135) 不动。
断言 (mock 数据, 390px):
  - burst td font = 11px (was 10)
  - color 仍 ink-3 (rgb 136,136,136, 弱信号)
  - sector/turnover/seal (R135) 仍 11px, score 11px (R128)
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
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:2,
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

        m = await page.evaluate(r"""() => {
          var row = document.querySelector('#bv-pick-tbody tr.bv-row');
          if (!row) return {none: true};
          var tds = row.querySelectorAll('td');
          function probe(idx) {
            var td = tds[idx];
            if (!td) return null;
            var cs = getComputedStyle(td);
            return {fontSize: cs.fontSize, color: cs.color, gridArea: cs.gridArea};
          }
          return {
            burst: probe(8),       // td:nth-child(9) — R137 守护
            sector: probe(2),      // td:nth-child(3) — R135 守护
            turnover: probe(4),    // td:nth-child(5) — R135 守护
            seal: probe(6),        // td:nth-child(7) — R135 守护
            tdCount: tds.length
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("tdCount", 0) >= 9, f"expected ≥9 tds, got {m.get('tdCount')}"
        assert m.get("burst", {}).get("fontSize") == "11px", f"burst should be 11px (was 10), got {m.get('burst', {}).get('fontSize')}"
        # R135 守护
        assert m.get("sector", {}).get("fontSize") == "11px", f"sector regression: {m.get('sector', {}).get('fontSize')} (R135 must stay 11)"
        assert m.get("turnover", {}).get("fontSize") == "11px", f"turnover regression: {m.get('turnover', {}).get('fontSize')} (R135 must stay 11)"
        assert m.get("seal", {}).get("fontSize") == "11px", f"seal regression: {m.get('seal', {}).get('fontSize')} (R135 must stay 11)"

        await browser.close()
        print(f"[OK] R137 burst — 11px (was 10) | R135 三格 11px ✓")

if __name__ == "__main__":
    asyncio.run(run())