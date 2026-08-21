"""R135 .bv-table td:nth-child(3/5/7) sector+turnover+seal 字号 10→11px — grid 三格 typography.

原: R77/R79 加了换手格内容 (量比 + 成交额), 但 td:nth-child(3) sector + (5) turnover + (7) seal
    三格 font 仍 10px, 跟 R108/R119/R120/R121/R126 typography 体系 11/11.5 不一致。
R135: 三格 10→11px (跟 typography 体系一致), color ink-2 中灰保留 (R134 同步)。
第一性原理: grid 三格内容都是元信息 (板块 / 换手 / 封单), 用户扫"该股基本面"主看这三格,
    10px 让元信息难凑近看清。11px 跟主信息 13px (name) 比例 0.85 (R108 meta 11.5/13 ratio 0.88)。
R77/R79 守护: turnover 双行内容 (换手 + 量比 + 成交额) 不变, 只放大字号。
断言 (mock 数据, 390px):
  - sector / turnover / seal 三格 font 都是 11px (was 10)
  - color 仍是 ink-2 (rgb(51, 65, 85) navy, R134 同步)
  - streak (td:6) 11px / change (td:4) 15px / name (td:2) 13px 不动 (R15/R10 守护)
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
            sector: probe(2),     // td:nth-child(3)
            turnover: probe(4),   // td:nth-child(5)
            seal: probe(6),       // td:nth-child(7)
            name: probe(1),       // td:nth-child(2) — R15 守护
            change: probe(3),     // td:nth-child(4) — R10 守护
            tdCount: tds.length
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("tdCount", 0) >= 8, f"expected ≥8 tds, got {m.get('tdCount')}"
        assert m.get("sector", {}).get("fontSize") == "11px", f"sector should be 11px (was 10), got {m.get('sector', {}).get('fontSize')}"
        assert m.get("turnover", {}).get("fontSize") == "11px", f"turnover should be 11px (was 10), got {m.get('turnover', {}).get('fontSize')}"
        assert m.get("seal", {}).get("fontSize") == "11px", f"seal should be 11px (was 10), got {m.get('seal', {}).get('fontSize')}"
        # name + change 守护 (R15 / R10)
        assert m.get("name", {}).get("fontSize") == "13px", f"name regression: {m.get('name', {}).get('fontSize')} (must stay 13)"
        assert m.get("change", {}).get("fontSize") == "15px", f"change regression: {m.get('change', {}).get('fontSize')} (must stay 15)"

        await browser.close()
        print(f"[OK] R135 grid cells — sector/turnover/seal 11px (was 10) | name 13px + change 15px (R15/R10 ✓)")

if __name__ == "__main__":
    asyncio.run(run())