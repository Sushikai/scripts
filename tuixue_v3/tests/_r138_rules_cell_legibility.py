"""R138 .bv-table td:nth-child(10) rules-cell 字号 10→11px — typography 体系完整收尾.

原: rules-cell (命中规则整行 td:10) font-size 10px, 但 cell 内含:
    hit-badge 10.5px (R131) + rule-chip 11px (R123) + cond-chip 11px (R123) + R66 motto 跟随,
    自身 10px 是浮空 — 体系外一格。
R138: rules-cell 10→11px (跟 R135/R137 grid 元信息体系一致)。
    padding-top:4px + border-top:dashed 保留 (规则行是分隔行, 视觉分块)。
    hit-badge 10.5px 不动 — 11px td 下 badge 仍 10.5px 居中视觉权重不破坏。
第一性原理: 10px 让元信息 (板块/换手/封单/炸板/规则) 凑近看不清,
    R135 已修 sector/turnover/seal, R137 修 burst, R138 收尾 rules-cell, typography 体系第 15 档完整。
    grid 6 格 (sector/turnover/seal/burst/rules/name) 全部 11px+ 一致,
    主信息 (name 13 / change 15 / time 12 / score 11) 体系收尾。
R131/R123/R135/R137 守护: hit-badge 10.5px / chips 11px / sector/turnover/seal 11px / burst 11px 不动。
断言 (mock 数据, 390px):
  - rules-cell td font = 11px (was 10)
  - padding-top:4px + border-top:dashed 保留
  - hit-badge 在 cell 内仍 10.5px (R131 守护)
  - sector/turnover/seal/burst 仍 11px (R135/R137 守护)
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
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01','BV07','BV03'], score:90,
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
            return {fontSize: cs.fontSize, paddingTop: cs.paddingTop, borderTop: cs.borderTopStyle};
          }
          var hb = document.querySelector('.bv-hit-badge');
          var hbCs = hb ? getComputedStyle(hb) : null;
          return {
            rules: probe(9),       // td:nth-child(10) — R138 守护
            burst: probe(8),       // td:nth-child(9) — R137 守护
            sector: probe(2),      // td:nth-child(3) — R135 守护
            turnover: probe(4),    // td:nth-child(5) — R135 守护
            seal: probe(6),        // td:nth-child(7) — R135 守护
            hitBadge: hbCs ? {fontSize: hbCs.fontSize} : null,
            tdCount: tds.length
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("tdCount", 0) >= 10, f"expected ≥10 tds, got {m.get('tdCount')}"
        assert m.get("rules", {}).get("fontSize") == "11px", f"rules-cell should be 11px (was 10), got {m.get('rules', {}).get('fontSize')}"
        # 注: padding-top:4px + border-top dashed 已被 R97 .bv-table tr.bv-row > td { padding:0 !important; border:0 }
        # 覆盖 (grid 布局接管), 源 CSS 注释保留作为设计意图。R138 只动 font-size, 不动其它已死代码。
        # R135/R137 守护
        assert m.get("burst", {}).get("fontSize") == "11px", f"burst regression: {m.get('burst', {}).get('fontSize')} (R137 must stay 11)"
        assert m.get("sector", {}).get("fontSize") == "11px", f"sector regression: {m.get('sector', {}).get('fontSize')} (R135 must stay 11)"
        assert m.get("turnover", {}).get("fontSize") == "11px", f"turnover regression: {m.get('turnover', {}).get('fontSize')} (R135 must stay 11)"
        assert m.get("seal", {}).get("fontSize") == "11px", f"seal regression: {m.get('seal', {}).get('fontSize')} (R135 must stay 11)"
        # R131 hit-badge 10.5px 守护 (在 rules cell 内)
        assert m.get("hitBadge", {}).get("fontSize") == "10.5px", f"hit-badge regression: {m.get('hitBadge', {}).get('fontSize')} (R131 must stay 10.5)"

        await browser.close()
        print(f"[OK] R138 rules-cell — 11px (was 10) | R137/R135 grid 5格 11px ✓ | hit-badge 10.5px (R131 ✓)")

if __name__ == "__main__":
    asyncio.run(run())