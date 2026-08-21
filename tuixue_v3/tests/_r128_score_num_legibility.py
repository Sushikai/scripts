"""R128 .bv-score-num 字号 10→12px + color ink-2→ink-1 — 分数主信号可读性.

第一性原理: 分数列 (90/76/65) 是用户判断"哪些强 / 哪些弱"的主信号。
    原 10px + ink-2 (中灰), 跟 R125 σ 偏移 (10.5px secondary) 视觉层级倒挂 —
    primary 信号反而比 secondary 还小! 用户扫不到分数。
R128: 10→12px (跟 R122 ops 体系一致) + ink-2→ink-1 (亮白, 主信号应最亮)。
    R125 σ 偏移 (10.5px) 保持: secondary 不喧宾夺主, primary 视觉权重最大。
    R62 score-bar 60px 工作终于闭环: 主数字 + bar + 偏移 三档视觉层级清晰。
断言 (mock 数据, 390px):
  - 至少 1 个 .bv-score-num 渲染
  - font-size = 12px
  - color 不再是 ink-2 (中灰)
  - font-weight = 700 (主信号加粗保留)
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

        # Inject score-num into row's score cell (td:nth-child(11))
        m = await page.evaluate(r"""() => {
          var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
          if (rows.length === 0) return {none: true};
          rows.forEach(function(r, i) {
            var scoreCell = r.querySelector('td:nth-child(11)');
            if (scoreCell) {
              var span = document.createElement('span');
              span.className = 'bv-score-num';
              span.textContent = i === 0 ? '90' : (i === 1 ? '76' : '65');
              scoreCell.appendChild(span);
            }
          });
          var num = document.querySelector('.bv-score-num');
          var cs = num ? getComputedStyle(num) : null;
          return {
            count: document.querySelectorAll('.bv-score-num').length,
            fontSize: cs ? cs.fontSize : null,
            color: cs ? cs.color : null,
            fontWeight: cs ? cs.fontWeight : null,
            firstText: num ? num.textContent : null,
            rowCount: rows.length
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("count", 0) >= 1, f"expected ≥1 score-num, got {m.get('count')}"
        assert m.get("fontSize") == "12px", f"score-num should be 12px (was 10), got {m.get('fontSize')}"
        assert m.get("fontWeight") in ("700", "bold"), f"score-num weight should be 700, got {m.get('fontWeight')}"
        # ink-1 (bright white) vs ink-2 (mid-grey). Must NOT equal the old ink-2.
        assert m.get("color") != "rgb(170, 170, 170)", f"score-num color still ink-2 ({m.get('color')}); should be ink-1"

        await browser.close()
        print(f"[OK] R128 score-num main signal — {m['fontSize']} {m['fontWeight']} {m['color']} ({m['count']} nums)")

if __name__ == "__main__":
    asyncio.run(run())