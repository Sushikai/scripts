"""R143 mobile .bv-scorepart font 10→11px — 详情内分数组成 typography 收尾.

原: bv-scorepart (R62 加的"分数组成"行, 详情内分数拆解 + 平均参考线) font 10px,
    详情内同档元信息 (R119 label 11px / R120 sub 11.5px / R125 σ 10.5px) typography 浮空。
R143: bv-scorepart 10→11px (跟 R119/R125 体系一致)。
第一性原理: 详情是用户展开深度看的场景 (R60 滚动到卡 / R61 accordion), 字号应比正面 10px 更舒展,
    10px 凑近看浪费时间, 11px 跟 R119 detail-label/R125 σ 一致 = 详情 typography 体系。
R62/R119/R125 守护: 分数条 4px/颜色/ratio-id minWidth 48px / σ badge 10.5px 不动。
R128 守护: 正面分数数字 12px ink-1 不动。
断言 (mock 数据, 390px, 注入 bv-scorepart 行):
  - scorepart font = 11px (was 10)
  - flex layout + gap + alignItems 不动
  - scorepart-id minWidth 48px 不动 (R62 守护)
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大仓环境判断', extracted_at:'2026-08-17',
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
          var vb = document.querySelector('.view-bv');
          if (vb) vb.hidden = false;
          var sp = document.createElement('div');
          sp.className = 'bv-scorepart';
          var id = document.createElement('span');
          id.className = 'bv-scorepart-id';
          id.textContent = 'BV01';
          sp.appendChild(id);
          var bar = document.createElement('div');
          bar.className = 'bv-scorepart-bar';
          sp.appendChild(bar);
          (vb || document.body).appendChild(sp);
          void document.body.offsetHeight;
          var el = document.querySelector('.bv-scorepart');
          var idEl = document.querySelector('.bv-scorepart-id');
          if (!el) return {none: true};
          var cs = getComputedStyle(el);
          var idCs = idEl ? getComputedStyle(idEl) : null;
          return {
            scorepart: {fontSize: cs.fontSize, display: cs.display, gap: cs.gap, alignItems: cs.alignItems},
            scorepartId: idCs ? {minWidth: idCs.minWidth, fontWeight: idCs.fontWeight} : null
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("scorepart", {}).get("fontSize") == "11px", f"scorepart should be 11px (was 10), got {m.get('scorepart', {}).get('fontSize')}"
        assert m.get("scorepart", {}).get("display") == "flex", f"scorepart display regression: {m.get('scorepart', {}).get('display')} (must stay flex)"
        assert m.get("scorepart", {}).get("gap") == "6px", f"scorepart gap regression: {m.get('scorepart', {}).get('gap')} (must stay 6px)"
        # R62 守护
        assert m.get("scorepartId", {}).get("minWidth") == "48px", f"scorepart-id minWidth regression: {m.get('scorepartId', {}).get('minWidth')} (R62 must stay 48)"

        await browser.close()
        print(f"[OK] R143 scorepart — 11px (was 10) | display flex + gap 6px + id minW 48px (R62 ✓)")

if __name__ == "__main__":
    asyncio.run(run())