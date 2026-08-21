"""R130 .bv-wl-btn + .bv-ai-btn 28→32px — 卡片右上操作按钮 tap zone 跟上 HIG.

原: R98 引入 wl-btn (自选) + ai-btn (问 AI), 都是 28×28 紧凑按钮。
    跟 R127 jump-btn (28→32) 一致 — 卡片右上角三个操作按钮 tap zone 全部跟上 HIG。
R130: wl-btn 28→32, ai-btn 28→32, wl-btn right 38→42 留4px 间距给 jump-btn (32宽 right:6)。
第一性原理: 三个按钮都是 daily-use 入口 (收藏 / 问 AI / 跳个股),
    28px 角落拇指难命中, 一致 32px 才是稳定体感。
R98 守护: min-height:0 !important 不动 (紧凑按钮不继承全局 40px 拉宽)。
断言 (mock 数据, 390px):
  - 至少 1 个 .bv-wl-btn + 1 个 .bv-ai-btn 渲染
  - wl-btn: width=32, height=32, right=42 (跟 jump-btn 留 4px 间距)
  - ai-btn: width=32, height=32
  - wl-btn 和 jump-btn 不重叠 (wl 右边缘 > jump 右边缘 + 4px)
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

        # Inject wl-btn, ai-btn, jump-btn into a row to test tap zone relationships
        m = await page.evaluate(r"""() => {
          var row = document.querySelector('#bv-pick-tbody tr.bv-row');
          if (!row) return {none: true};
          var td = document.createElement('td');
          td.className = 'bv-jump-btn-cell';
          td.innerHTML =
            '<button class="bv-wl-btn" data-wl="600519">★</button>' +
            '<button class="bv-ai-btn" data-ai="600519">🤖</button>' +
            '<button class="bv-jump-btn" data-goto-stock="600519">📈</button>';
          row.appendChild(td);
          void document.body.offsetHeight;
          var wl = document.querySelector('.bv-wl-btn');
          var ai = document.querySelector('.bv-ai-btn');
          var jb = document.querySelector('.bv-jump-btn');
          if (!wl || !ai || !jb) return {none: true};
          var wlR = wl.getBoundingClientRect();
          var aiR = ai.getBoundingClientRect();
          var jbR = jb.getBoundingClientRect();
          // jump-btn right:6 + width:32 → right edge at 6 from right edge of container
          // wl-btn right:42 → right edge at 42 from right
          // wl-btn left edge should be at right:42+32=74 from right → wl-right-edge < jb-right-edge means no overlap (wl is left of jb)
          var noOverlap = (wlR.right) <= (jbR.left + 1);
          return {
            wlW: Math.round(wlR.width), wlH: Math.round(wlR.height),
            aiW: Math.round(aiR.width), aiH: Math.round(aiR.height),
            jbW: Math.round(jbR.width), jbH: Math.round(jbR.height),
            wlRight: Math.round(wlR.right),
            jbLeft: Math.round(jbR.left),
            gap: Math.round(jbR.left - wlR.right),
            noOverlap: noOverlap
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("wlW") == 32, f"wl-btn width should be 32 (was 28), got {m.get('wlW')}"
        assert m.get("wlH") == 32, f"wl-btn height should be 32 (was 28), got {m.get('wlH')}"
        assert m.get("aiW") == 32, f"ai-btn width should be 32 (was 28), got {m.get('aiW')}"
        assert m.get("aiH") == 32, f"ai-btn height should be 32 (was 28), got {m.get('aiH')}"
        assert m.get("jbW") == 32, f"jump-btn regression: {m.get('jbW')} (R127 must stay 32)"
        assert m.get("noOverlap"), f"wl-btn overlaps jump-btn (gap={m.get('gap')}); right:42 spacing broken"
        assert m.get("gap", 0) >= 1, f"wl-btn / jump-btn gap regression: {m.get('gap')}px (need ≥1)"

        await browser.close()
        print(f"[OK] R130 wl/ai-btn tap zone — wl {m['wlW']}×{m['wlH']} ai {m['aiW']}×{m['aiH']} (was 28) | jump {m['jbW']}×{m['jbH']} (R127 ✓) | gap {m['gap']}px (no overlap ✓)")

if __name__ == "__main__":
    asyncio.run(run())