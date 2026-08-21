"""R148 mobile .bv-vr 量比小字 9→10.5px — 资金核心信号 chip 地板对齐.

原: R77 加的"换手格量比小字" (.bv-vr), font 9px — 注释自己写"资金放大/缩量核心信号",
    但 9px 是全页数据 chips 中最低一档, 低于 typography 体系 chip 地板 10.5px
    (R125 σ badge 10.5 / R131 hit-badge 10.5 / R132 motto 10.5)。
    量比是用户判断"今天资金在不在"的第一顺位 (放量→有资金, 缩量→没资金),
    9px 在 390px 屏幕上几乎不可读, 用户得眯眼看 — 违背信息要一次看懂的信号层级。
R148: bv-vr 9→10.5px (chip 地板统一), weight 700 保留 (它是强调信号)。
第一性原理: 量比是"资金在场证明" (放量 2.5+ 表示今天有增量资金),
    跟 σ (偏离度) / hit-badge (命中数) 同级属于数据 chips, 必须共享同一可读地板 —
    任何一个 chip 掉到 10px 以下都会让用户以为它"不重要" (视觉=语义)。
R77 守护: inline-block + margin-left 4 + padding 0 3 + radius 3 + weight 700 + line-height 14 不动。
    bv-vr-hot (#4ade80) / bv-vr-neg 颜色不动。
断言 (mock 数据, 390px, 注入 vr chip):
  - bv-vr font = 10.5px (was 9)
  - font-weight 700 保留
  - line-height 14px 保留
  - padding 0 3px 保留
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
          var row = document.querySelector('#bv-pick-tbody tr.bv-row') || document.querySelector('body');
          var span = document.createElement('span');
          span.className = 'bv-vr';
          span.textContent = '量比2.1';
          row.appendChild(span);
          void document.body.offsetHeight;
          var el = document.querySelector('.bv-vr');
          if (!el) return {none: true};
          var cs = getComputedStyle(el);
          return {
            fontSize: cs.fontSize,
            fontWeight: cs.fontWeight,
            lineHeight: cs.lineHeight,
            padding: cs.padding,
            display: cs.display,
            marginLeft: cs.marginLeft,
            borderRadius: cs.borderRadius
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("fontSize") == "10.5px", f"bv-vr should be 10.5px (was 9), got {m.get('fontSize')}"
        assert m.get("fontWeight") == "700", f"bv-vr weight regression: {m.get('fontWeight')} (must stay 700)"
        # R77 守护
        assert m.get("lineHeight") == "14px", f"bv-vr line-height regression: {m.get('lineHeight')}"
        assert m.get("padding") and "3px" in m["padding"], f"bv-vr padding regression: {m.get('padding')}"

        await browser.close()
        print(f"[OK] R148 bv-vr — 10.5px (was 9) | weight 700 + lineH 14 (R77 ✓)")

if __name__ == "__main__":
    asyncio.run(run())
