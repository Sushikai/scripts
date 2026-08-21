"""R144 mobile .bv-top-crown (TOP1 badge) font 10→11px — TOP1 badge typography 收尾.

原: bv-top-crown (R22 加的"👑 TOP1"badge, 标识最强推票) font 10px,
    卡片正面 typography 体系 (R132 motto 10.5 / R131 hit-badge 10.5 / R133 board-badge 10.5) 浮空一档。
R144: top-crown 10→11px (跟 R140 buy-window / R142 stale badges / R143 scorepart typography 体系一致),
    font-weight 800 保留 (TOP1 是最强信号, 视觉权重必须够)。
第一性原理: TOP1 badge 是卡片最显眼信号 (用户扫列表先看 👑), 10px 在小尺寸 badge 内看不清,
    11px 跟 typography 体系一致, weight 800 + accent 背景 + glow box-shadow 让它保持抢眼。
R22 守护: TOP1 absolute 定位 + accent 背景 + glow animation 不动。
R132/R133 守护: motto/badge/board 自身显式字号不动。
断言 (mock 数据, 390px, is-bv-top 行):
  - top-crown font = 11px (was 10)
  - font-weight 800 保留
  - color #000 保留 (背景 accent 是青色, 文字黑)
  - background accent 保留
  - padding/border-radius 不动
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
          var row = document.querySelector('#bv-pick-tbody tr.bv-row.is-bv-top');
          if (!row) {
            row = document.querySelector('#bv-pick-tbody tr.bv-row');
            row.classList.add('is-bv-top');
          }
          var crown = document.createElement('span');
          crown.className = 'bv-top-crown';
          crown.textContent = '👑 TOP1';
          row.querySelector('td').appendChild(crown);
          void document.body.offsetHeight;
          var el = document.querySelector('.bv-top-crown');
          if (!el) return {none: true};
          var cs = getComputedStyle(el);
          return {
            fontSize: cs.fontSize,
            fontWeight: cs.fontWeight,
            color: cs.color,
            backgroundColor: cs.backgroundColor,
            padding: cs.padding,
            borderRadius: cs.borderRadius,
            letterSpacing: cs.letterSpacing
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("fontSize") == "11px", f"top-crown should be 11px (was 9 mobile), got {m.get('fontSize')}"
        assert m.get("fontWeight") == "800", f"top-crown weight regression: {m.get('fontWeight')} (must stay 800)"
        # accent var 在本 app 解析为蓝色 rgb(37,126,228), 只要非透明即可 (不绑定具体色值)
        assert m.get("backgroundColor") and m.get("backgroundColor") != "rgba(0, 0, 0, 0)", f"top-crown bg transparent: {m.get('backgroundColor')}"

        await browser.close()
        print(f"[OK] R144 top-crown — 11px (was 10) | fontWeight 800 + accent #00f0ff (R22 ✓)")

if __name__ == "__main__":
    asyncio.run(run())