"""R149 mobile .bv-sector-chg 板块涨幅小字 9→10.5px — 最后一个 9px 数据 chip.

原: R16 加的板块涨幅小字 (.bv-sector-chg, 板块名旁右上小字) font 9px weight 600。
    这是用户判断"这票所在板块今天强不强"的即时信号 (板块强势→个股有题材托底),
    9px 是全页最后一个低于 chip 地板 10.5px 的数据 chip (R148 bv-vr 已收, R149 收尾)。
R149: bv-sector-chg 9→10.5px (chip 地板统一), 跟 bv-sector-name 11px 保持 0.5px 层级差
    (名称是主体, 涨幅是附属信号, 但附属不等于不可读)。
第一性原理: 板块涨幅是"题材温度计" (板块红→资金在题材内, 板块绿→题材退潮),
    用户扫卡片时把它当第二眼信号读。9px 在 390px 下是 2.3mm 高, 低于 iOS 人眼舒适下限
    (正文 ≤10px 在高 DPI 屏上会被浏览器亚像素渲染模糊)。
    所有数据 chips 统一 10.5px 地板后, 页面不再有"意外小字", 扫读层级稳定。
R16 守护: weight 600 + padding 0 4 + radius 3 + line-height 14 + nowrap + pos/neg 颜色不动。
断言 (mock 数据, 390px, 注入 sector-chg chip):
  - bv-sector-chg font = 10.5px (was 9)
  - font-weight 600 保留
  - line-height 14px 保留
  - white-space nowrap 保留
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
          span.className = 'bv-sector-chg bv-pos';
          span.textContent = '+3.2%';
          row.appendChild(span);
          void document.body.offsetHeight;
          var el = document.querySelector('.bv-sector-chg');
          if (!el) return {none: true};
          var cs = getComputedStyle(el);
          return {
            fontSize: cs.fontSize,
            fontWeight: cs.fontWeight,
            lineHeight: cs.lineHeight,
            padding: cs.padding,
            whiteSpace: cs.whiteSpace,
            color: cs.color
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("fontSize") == "10.5px", f"bv-sector-chg should be 10.5px (was 9), got {m.get('fontSize')}"
        assert m.get("fontWeight") == "600", f"bv-sector-chg weight regression: {m.get('fontWeight')} (must stay 600)"
        # R16 守护
        assert m.get("lineHeight") == "14px", f"bv-sector-chg line-height regression: {m.get('lineHeight')}"
        assert m.get("whiteSpace") == "nowrap", f"bv-sector-chg nowrap regression: {m.get('whiteSpace')}"

        await browser.close()
        print(f"[OK] R149 bv-sector-chg — 10.5px (was 9) | weight 600 + nowrap (R16 ✓)")

if __name__ == "__main__":
    asyncio.run(run())
