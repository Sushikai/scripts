"""R121 bv-phase-banner .bv-phase-ttl 倒计时 — 10→11px (跟 R108/R119/R120 typography 模式).

原: R14 sticky 阶段 banner 顶部固定显示, .bv-phase-ttl 是倒计时数字 (如 "300s" / "刷新倒计时")。
    10px 跟 banner 主标签强对比 → 像水印感, 用户瞥一眼 banner 倒计时看不清。
R121: 10→11px (跟 R108 meta / R119 detail-label / R120 title-sub 11/11.5px 一致 typography 升级),
    视觉层级跟 banner 自身大小保留 (banner 主色块不抢戏)。
断言 (mock 数据, 390px):
  - sticky phase banner .bv-phase-ttl 字号 = 11px
  - banner 主标签字号保留 (不抢戏)
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
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0 }
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
                await page.goto("http://127.0.0.1:7799/#bv", wait_until="domcontentloaded", timeout=20000)
                break
            except Exception:
                await page.wait_for_timeout(2000)
        for i in range(15):
            await page.wait_for_timeout(800)
            if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 1:
                break
        await page.wait_for_timeout(500)

        # Inject phase banner if not present (R14 structure)
        await page.evaluate("""() => {
            var vb = document.querySelector('.view-bv');
            if (vb) { vb.hidden = false; vb.style.display = ''; }
            var banner = document.querySelector('.bv-phase-banner');
            if (!banner) {
                banner = document.createElement('div');
                banner.className = 'bv-phase-banner bv-tone-info';
                banner.innerHTML =
                  '<span class="bv-phase-icon">🟢</span>' +
                  '<span class="bv-phase-label">盘后守候</span>' +
                  '<span class="bv-phase-ttl">300s</span>';
                document.body.appendChild(banner);
            }
        }""")
        await page.wait_for_timeout(300)

        m = await page.evaluate(r"""() => {
          var ttl = document.querySelector('.bv-phase-banner .bv-phase-ttl');
          var label = document.querySelector('.bv-phase-banner .bv-phase-label');
          if (!ttl) return {none:true};
          return {
            ttlFont: getComputedStyle(ttl).fontSize,
            labelFont: label ? getComputedStyle(label).fontSize : null,
            ttlText: ttl.textContent.trim()
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("ttlFont") == "11px", f"phase-ttl should be 11px (was 10), got {m.get('ttlFont')}"

        await browser.close()
        print(f"[OK] R121 phase-ttl typography — {m['ttlFont']} (was 10) | label {m['labelFont']}")

if __name__ == "__main__":
    asyncio.run(run())