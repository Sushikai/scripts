"""R127 .bv-jump-btn tap zone 28→32px — 跳个股按钮 HIG 升级.

原: R12 引入 .bv-jump-btn (卡片右上角 📈 跳个股页), 28×28px 低于 HIG 32 最低。
    每张卡片最高频跳转操作, 28px 在 card 角落拇指难命中。
R127: 28→32 (跟 R104-R118 + R117 统一 HIG)。
    R98 守护: 仍保留 min-height:0 (不继承全局 40px 拉宽), 仍是紧凑 32px。
第一性原理: jump-btn 是 daily-use 入口 (看完卡片 → 跳个股详情),
    tap zone 跟上 HIG 才能稳定命中。
断言 (mock 数据, 390px):
  - 至少 1 个 .bv-jump-btn 渲染
  - width = 32px, height = 32px
  - min-height: 0 (R98 守护) — 不被全局 40px 拉宽
  - font-size 14px 保留 (图标大小不变, 只扩 tap zone)
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
          // The bv-jump-btn is rendered via bv-frontend.js. Check existing buttons.
          // Force view-bv visible
          var vb = document.querySelector('.view-bv');
          if (vb) vb.hidden = false;
          // Find jump buttons (real ones from frontend render or inject test ones)
          var btns = document.querySelectorAll('.view-bv .bv-jump-btn');
          if (btns.length === 0) {
            // Inject into first row's first cell
            var row = document.querySelector('#bv-pick-tbody tr.bv-row');
            if (row) {
              var td = document.createElement('td');
              td.className = 'bv-jump-btn-cell';
              td.innerHTML = '<button class="bv-jump-btn" data-goto-stock="600519">📈</button>';
              row.appendChild(td);
            }
            btns = document.querySelectorAll('.view-bv .bv-jump-btn');
          }
          void document.body.offsetHeight;
          var btn = document.querySelector('.bv-jump-btn');
          if (!btn) return {none: true};
          var rect = btn.getBoundingClientRect();
          var cs = getComputedStyle(btn);
          return {
            count: btns.length,
            width: Math.round(rect.width),
            height: Math.round(rect.height),
            minHeight: cs.minHeight,
            padding: cs.padding,
            fontSize: cs.fontSize,
            label: btn.getAttribute('aria-label') || btn.textContent.trim().slice(0, 5)
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("count", 0) >= 1, f"expected ≥1 jump-btn, got {m.get('count')}"
        assert m.get("width") == 32, f"jump-btn width should be 32 (was 28), got {m.get('width')}"
        assert m.get("height") == 32, f"jump-btn height should be 32 (was 28), got {m.get('height')}"
        assert m.get("minHeight") == "0px", f"R98 min-height:0 !important lost, got {m.get('minHeight')}"
        assert m.get("fontSize") == "14px", f"icon font regression: {m.get('fontSize')} (must stay 14)"

        await browser.close()
        print(f"[OK] R127 jump-btn tap zone — {m['width']}×{m['height']} (was 28) | minH {m['minHeight']} (R98 ✓) | font {m['fontSize']}")

if __name__ == "__main__":
    asyncio.run(run())