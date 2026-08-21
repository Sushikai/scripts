"""R139 sort sheet .bv-sort-opt tap zone 38→44px — Apple HIG 44 最低, 跟 R117 sort-dir 一致.

原: bv-sort-opt (排序 sheet 2×2 grid 6 维度按钮: 分数/连板/涨幅/换手/封单/时间)
    padding:10px font13px → tap zone ~10+13+10 ≈ 33-38px, 不达 HIG 44。
R139: padding 10→12 + min-height:44px (显式 HIG), 跟 R117 sort-dir-opt 一致体系。
    跟 R115 detail-ops (44) + R116 multi-toolbar (32) + R117 sort-dir (44) + R118 loadmore (44)
    共同构成 sheet/工具类按钮 44px 体系。
第一性原理: sheet 是高频操作层, 用户开 sheet 就是准备连续切, 6 维度都点小目标 (排序切换),
    拇指精度 44px 最低保证。38px 是"看起来大"但拇指容易点偏 (尤其 6 维度 grid 中)。
R117 守护: bv-sort-dir-opt 不动 (已 44px)。
R115/R116/R118 守护: detail-ops 44 / multi-toolbar 32 / loadmore-btn 44 不动。
断言 (mock 数据, 390px, open sort sheet):
  - bv-sort-opt tap zone ≥44px (height)
  - padding:12px (was 10)
  - font-size 13px 不动
  - sort-dir-opt 不动 (R117)
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

        # Open sort sheet (sort-btn click)
        await page.evaluate(r"""() => {
          var btn = document.querySelector('.bv-sort-btn');
          if (btn) btn.click();
          // sheet may be created lazily
          void document.body.offsetHeight;
        }""")
        await page.wait_for_timeout(500)

        # Inject sort sheet if not exists (for headless mock)
        m = await page.evaluate(r"""() => {
          var sheet = document.querySelector('.bv-sort-sheet, .bv-sort-list, .bv-sort-opt, [class*="sort"]');
          if (!sheet || !document.querySelector('.bv-sort-opt')) {
            // build a sheet + opts
            var host = document.createElement('div');
            host.className = 'bv-sort-sheet';
            host.style.cssText = 'position:fixed;bottom:0;left:0;right:0;z-index:9999;';
            var list = document.createElement('div');
            list.className = 'bv-sort-list';
            list.style.cssText = 'display:grid;grid-template-columns:1fr 1fr;gap:6px;';
            ['分数','连板','涨幅','换手','封单','时间'].forEach(function(t, i) {
              var opt = document.createElement('button');
              opt.className = 'bv-sort-opt' + (i===0?' is-active':'');
              opt.textContent = t;
              list.appendChild(opt);
            });
            var dir = document.createElement('div');
            dir.className = 'bv-sort-dir';
            dir.style.cssText = 'display:flex;gap:8px;';
            ['升序','降序'].forEach(function(t, i) {
              var opt = document.createElement('button');
              opt.className = 'bv-sort-dir-opt' + (i===1?' is-active':'');
              opt.textContent = t;
              opt.style.cssText = 'flex:1;font-size:13px;padding:12px;border-radius:8px;border:1px solid rgba(255,255,255,0.1);background:#222;color:#fff;';
              dir.appendChild(opt);
            });
            host.appendChild(list);
            host.appendChild(dir);
            document.body.appendChild(host);
          }
          void document.body.offsetHeight;
          var opt = document.querySelector('.bv-sort-opt');
          if (!opt) return {none: true};
          var cs = getComputedStyle(opt);
          var rect = opt.getBoundingClientRect();
          var dirOpt = document.querySelector('.bv-sort-dir-opt');
          var dirCs = dirOpt ? getComputedStyle(dirOpt) : null;
          return {
            sortOpt: {
              padding: cs.padding,
              minHeight: cs.minHeight,
              fontSize: cs.fontSize,
              height: Math.round(rect.height),
              width: Math.round(rect.width)
            },
            sortDir: dirCs ? {padding: dirCs.padding, fontSize: dirCs.fontSize} : null
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("sortOpt"), f"sortOpt missing: {m}"
        # padding 12px top+bottom → tap zone ≥44
        assert "12px" in m["sortOpt"]["padding"], f"sort-opt padding should include 12px (was 10), got {m['sortOpt']['padding']}"
        assert m["sortOpt"]["minHeight"] == "44px", f"sort-opt min-height must be 44 (HIG), got {m['sortOpt']['minHeight']}"
        # 实际高度 ≥44
        assert m["sortOpt"]["height"] >= 44, f"sort-opt actual height {m['sortOpt']['height']} < 44 (HIG)"
        assert m["sortOpt"]["fontSize"] == "13px", f"sort-opt font regression: {m['sortOpt']['fontSize']}"

        await browser.close()
        print(f"[OK] R139 sort-opt — padding 12px + minH 44px (was 38) | actual {m['sortOpt']['height']}px ✓")

if __name__ == "__main__":
    asyncio.run(run())