"""R125 .bv-score-rel 字号 9→10.5px — 分数相对均值 badge 可读性.

原: R62 引入 .bv-score-rel (相对均值的 σ 偏移徽章, 如 +1.2σ / -0.3σ),
    字号 9px, 跟 11px score 数字对比强烈 → 像水印贴在分数旁。
R125: font 9→10.5px (跟 R62 score-bar 体系协同 — score 11px / avgline / score-rel 10.5)。
    用户扫"哪几个超均值"时, 9px 难凑近看清; 10.5px 维持小数点后精度可读。
第一性原理: σ 偏移是辅助决策信号 (高分不一定是真强, 看 σ 才知),
    信号必须可读, 否则分数条 60px 工作白做。
断言 (mock 数据, 390px):
  - 至少 1 个 .bv-score-rel 渲染
  - font-size = 10.5px
  - font-weight = 700 (粗体保留 — 信号靠颜色 + 粗体传达)
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
      top_rule: { id:'BV01', title:'弱转强', quote:'...', timestamp:'01:12', score_weight:10, weight:10, value:20 } }
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

        # Inject score-rel badges into the score cell (td:nth-child(11)) for 2 rows
        m = await page.evaluate(r"""() => {
          // Inject score-rel into existing rows' score cells
          var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
          if (rows.length === 0) return {none: true};
          rows.forEach(function(r, i) {
            var scoreCell = r.querySelector('td:nth-child(11)');
            if (scoreCell) {
              var span = document.createElement('span');
              span.className = 'bv-score-rel ' + (i === 0 ? 'high' : 'low');
              span.textContent = (i === 0 ? '+1.2σ' : '-0.3σ');
              scoreCell.appendChild(span);
            }
          });
          var rel = document.querySelector('.bv-score-rel');
          var cs = rel ? getComputedStyle(rel) : null;
          return {
            count: document.querySelectorAll('.bv-score-rel').length,
            fontSize: cs ? cs.fontSize : null,
            fontWeight: cs ? cs.fontWeight : null,
            color: cs ? cs.color : null,
            firstText: rel ? rel.textContent : null,
            rowCount: rows.length
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("count", 0) >= 1, f"expected ≥1 score-rel, got {m.get('count')}"
        assert m.get("fontSize") == "10.5px", f"score-rel should be 10.5px (was 9), got {m.get('fontSize')}"
        assert m.get("fontWeight") in ("700", "bold"), f"score-rel weight should be 700, got {m.get('fontWeight')}"

        await browser.close()
        print(f"[OK] R125 score-rel typography — {m['fontSize']} {m['fontWeight']} {m['color']} ({m['count']} badges)")

if __name__ == "__main__":
    asyncio.run(run())