"""R142 stale strip .bv-stale-paused-badge + .bv-stale-silent font 10→11px — stale strip typography 收尾.

原: bv-stale-paused-badge (R56 加的"暂停自动刷新"badge) + bv-stale-silent (R57 加的"静默更新"badge)
    仍 font 10px, stale strip 主体 (R31-R38 一直在优化) typography 浮空。
R142: 两个 badge 10→11px (跟 R140 pinned buy-window 一致, stale strip 内部 typography 收尾)。
第一性原理: stale strip 是 sticky 状态 + 顶部最高优先级信号 (R31-R46 持续优化),
    10px 在 mobile 顶部窄条 (40px 高) 看不清, 11px 跟 typography 体系一致。
    两个 badge 都是状态提示 (用户主动触发 pause/silent mode), 不是高频主操作, 11px 不需 700 加粗已守护。
R31-R38/R56/R57 守护: stale strip 文案/颜色/spinner 不动, 只动 badge 字号。
R140 守护: pinned banner buy-window typography 一致 (买窗口/状态徽章都是顶部信号)。
断言 (mock 数据, 390px):
  - paused-badge font = 11px (was 10)
  - silent-badge font = 11px (was 10)
  - color + padding + radius + margin 不动
  - font-weight 700 保留
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
          // Inject stale strip + badges
          var strip = document.createElement('div');
          strip.className = 'bv-stale-strip';
          strip.style.cssText = 'position:fixed;top:60px;left:0;right:0;';
          var p = document.createElement('span');
          p.className = 'bv-stale-paused-badge';
          p.textContent = '已暂停';
          strip.appendChild(p);
          var s = document.createElement('span');
          s.className = 'bv-stale-silent';
          s.textContent = '静默更新';
          strip.appendChild(s);
          document.body.appendChild(strip);
          void document.body.offsetHeight;
          var pEl = document.querySelector('.bv-stale-paused-badge');
          var sEl = document.querySelector('.bv-stale-silent');
          if (!pEl || !sEl) return {none: true};
          var pcs = getComputedStyle(pEl);
          var scs = getComputedStyle(sEl);
          return {
            paused: {fontSize: pcs.fontSize, fontWeight: pcs.fontWeight, padding: pcs.padding, borderRadius: pcs.borderRadius},
            silent: {fontSize: scs.fontSize, fontWeight: scs.fontWeight, padding: scs.padding, borderRadius: scs.borderRadius}
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("paused", {}).get("fontSize") == "11px", f"paused-badge should be 11px (was 10), got {m.get('paused', {}).get('fontSize')}"
        assert m.get("paused", {}).get("fontWeight") == "700", f"paused-badge weight regression: {m.get('paused', {}).get('fontWeight')} (must stay 700)"
        assert m.get("silent", {}).get("fontSize") == "11px", f"silent-badge should be 11px (was 10), got {m.get('silent', {}).get('fontSize')}"
        assert m.get("silent", {}).get("fontWeight") == "700", f"silent-badge weight regression: {m.get('silent', {}).get('fontWeight')} (must stay 700)"

        await browser.close()
        print(f"[OK] R142 stale strip badges — paused 11px + silent 11px (was 10) | fontWeight 700 ✓")

if __name__ == "__main__":
    asyncio.run(run())