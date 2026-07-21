#!/usr/bin/env python3
"""
R56 6 套退场重构 · 视觉验证 · 5 大区域特写
  1) 顶部 KPI 卡 (含仓位换算 group)
  2) 6 套退场胜率表
  3) baseline↔WR1000 对比表
  4) 月度表 12 列 (6 套 avg + win_rate)
  5) 退场模型解释区块 (底部)
"""
import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:7799/#screener"
ART = "/Users/kaikai/scripts/tuixue_v3/web/tests/artifacts/r56_sections"
Path(ART).mkdir(parents=True, exist_ok=True)


async def post_bt(page, strategy_id, sample=50):
    body = {
        "periods": ["半年"], "hold_days": 1, "top_n": 2, "sample": sample,
        "breadth_min": 0, "breadth_min_soft": 0,
        "sector_hot_topn": 0, "sector_inflow_topn": 0,
        "require_surge_label": False, "enable_actual_10": False,
        "index_late_up": False, "sector_late_up": False,
        "tail_vol_ratio_min": 0, "strategy_id": strategy_id,
    }
    # 重试锁占用 (BT 互斥锁, 需等前一个完成)
    for attempt in range(30):
        r = await page.evaluate(
            """async (b) => {
                const r = await fetch('/api/screener/backtest', {
                    method: 'POST', headers: {'Content-Type':'application/json'},
                    body: JSON.stringify(b)
                });
                return await r.json();
            }""", body)
        if r.get("data", {}).get("run_id"):
            rid = r["data"]["run_id"]
            print(f"  [{strategy_id}] rid: {rid} (尝试 {attempt+1})")
            break
        if "已有回测在跑" in str(r):
            print(f"  [{strategy_id}] BT 锁占用, 等 3s 重试…")
            await page.wait_for_timeout(3000)
            continue
        print(f"  [{strategy_id}] POST fail: {r}")
        return None
    else:
        print(f"  [{strategy_id}] 30 次重试都失败")
        return None
    for i in range(60):
        await page.wait_for_timeout(3000)
        s = await page.evaluate(
            """async (rid) => {
                const r = await fetch('/api/screener/backtest?run_id=' + rid);
                return await r.json();
            }""", rid)
        st = s.get("data", {}).get("status")
        if st == "done":
            return s["data"].get("result")
        if st == "error":
            print(f"    err: {s.get('data', {}).get('error')}")
            return None
    print(f"    timeout")
    return None


async def render_to(page, result):
    """直接走前端 btRenderV4 渲染 (避免轮询)"""
    await page.evaluate(
        """(r) => {
            window._BT_RESULTS = window._BT_RESULTS || {};
            window._BT_RESULTS[r.config.strategy_id] = r;
            btRenderV4(r);
            // 手动模拟 btFinishRun hook: 触发 auto-compare
            // (因为 btFinishRun 内部 _btMaybeAutoCompare 是 closure, 不能直接调,
            //  通过设置标记 + 让 btStart 走完整路径最干净; 这里用按钮 click)
            window._BT_RESULT_SIG = window._BT_RESULT_SIG || {};
            window._BT_RESULT_SIG[r.config.strategy_id] = JSON.stringify({
                periods: r.config.period_keys, hold: r.config.hold_days, top: r.config.top_n,
                sample: r.config.sample_size
            });
            // 直接调 btRenderCompare (这个是暴露在 window 上的)
            if (typeof window.btRenderCompare === 'function') {
                window.btRenderCompare('manual');
            }
        }""", result)


async def crop_section(page, selector, out_path, padding=8):
    """滚动到元素 + 截该元素 (而非全页)"""
    el = page.locator(selector).first
    if not await el.count():
        print(f"  ⚠ selector missing: {selector}")
        return False
    await el.scroll_into_view_if_needed()
    await page.wait_for_timeout(150)
    await el.screenshot(path=out_path)
    print(f"  ✓ {selector} → {os.path.basename(out_path)}")
    return True


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        page.on("pageerror", lambda e: print(f"  [pageerror] {e}"))

        print("→ 打开", URL)
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        # 等 DOM 加载,不强求 .bt-tab visible (可能藏在 collapsed panel 内)
        await page.wait_for_selector(".bt-tab", state="attached", timeout=15000)
        await page.evaluate("""() => {
            typeof showView === 'function' && showView('screener', {push: false});
            const p = document.getElementById('backtest-panel');
            if (p) p.classList.remove('collapsed');
            // 切到 baseline tab (避免 WR1000 抢占 active 状态)
            const tabs = document.querySelectorAll('.bt-tab');
            tabs.forEach(t => t.classList.remove('active'));
            const baseTab = document.querySelector(".bt-tab[data-strategy='baseline']");
            if (baseTab) baseTab.classList.add('active');
        }""")
        await page.wait_for_timeout(1500)

        print("→ 跑 baseline…")
        r1 = await post_bt(page, "baseline")
        if not r1:
            print("  baseline 失败")
            return

        print("→ 跑 WIN_RATE_1000…")
        r2 = await post_bt(page, "WIN_RATE_1000")
        if not r2:
            print("  WR1000 失败")
            return

        await render_to(page, r1)
        await render_to(page, r2)
        await page.wait_for_timeout(800)

        await page.wait_for_timeout(800)

        # ===== 顶部 KPI 卡 =====
        print("\n=== 1) KPI 卡 (含仓位换算) ===")
        await crop_section(page, "#bt-kpis", f"{ART}/01_kpis.png")

        # ===== 6 套退场胜率表 =====
        print("\n=== 2) 6 套退场胜率表 ===")
        await crop_section(page, "#bt-exits-compare-host", f"{ART}/02_exits_6.png")

        # ===== baseline↔WR1000 对比表 =====
        print("\n=== 3) baseline↔WR1000 对比表 ===")
        await crop_section(page, "#bt-compare-host", f"{ART}/03_compare_baseline_wr1000.png")

        # ===== 月度表 12 列 =====
        print("\n=== 4) 月度表 (6 套多列) ===")
        await crop_section(page, "#bt-monthly-host, #bt-monthly", f"{ART}/04_monthly_12cols.png")

        # ===== 退场模型解释 =====
        print("\n=== 5) 退场模型解释区块 ===")
        await crop_section(page, "#bt-exit-model-doc", f"{ART}/05_exit_model_doc.png")

        # ===== 底部策略规则说明 =====
        print("\n=== 6) 底部策略规则说明 ===")
        await crop_section(page, "#bt-strategy-rules", f"{ART}/06_strategy_rules.png")

        # ===== 全页 baseline =====
        print("\n=== 7) 全页 (baseline 视角) ===")
        await page.screenshot(path=f"{ART}/07_fullpage_baseline.png", full_page=True)

        # ===== 切到 WR1000 =====
        print("\n=== 8) Tab → WR1000 全页 ===")
        try:
            await page.click(".bt-tab[data-strategy='WIN_RATE_1000']", timeout=3000)
            await page.wait_for_timeout(800)
        except Exception:
            print("  no WR1000 tab (auto-compare 没成功, 跳过)")
        await page.screenshot(path=f"{ART}/08_fullpage_wr1000.png", full_page=True)

        # ===== 移动端 =====
        print("\n=== 9) 移动端 (iPhone 13) ===")
        m_ctx = await browser.new_context(viewport={"width": 390, "height": 844})
        m_page = await m_ctx.new_page()
        await m_page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await m_page.wait_for_timeout(2000)
        # 强制导航到 screener + 展开 backtest panel + 跑 baseline
        try:
            await m_page.evaluate("""async () => {
                // 切到 screener view
                const screenerNav = document.querySelector("[data-nav='screener'], [data-view='screener']")
                    || document.querySelector("a[href*='screener']");
                if (typeof showView === 'function') showView('screener', {push: false});
                // 展开回测面板
                const p = document.getElementById('backtest-panel');
                if (p) p.classList.remove('collapsed');
                // 滚动到 #bt-run 按钮位置
                const btn = document.getElementById('bt-run');
                if (btn) btn.scrollIntoView({behavior: 'instant', block: 'center'});
            }""")
            await m_page.wait_for_timeout(500)

            # 直接调 API + 渲染 (绕过 btStart 的页面交互, 稳)
            r_mobile = await post_bt(m_page, "baseline", sample=60)
            if r_mobile:
                await render_to(m_page, r_mobile)
                await m_page.wait_for_timeout(800)
                # 滚动到顶部 (让 KPI 卡可见)
                await m_page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.05)")
                await m_page.wait_for_timeout(300)
                print("  移动端 baseline 已渲染")
            else:
                print("  移动端 baseline 失败, 截图纯页面")
        except Exception as e:
            print(f"  移动端 setup 异常: {e}")
        await m_page.wait_for_timeout(800)
        await m_page.screenshot(path=f"{ART}/09_mobile_baseline.png", full_page=True)

        # KPI 移动端 (用 evaluate 注入可视, 不依赖滚动)
        try:
            kpi_box = await m_page.evaluate("""() => {
                const k = document.getElementById('bt-kpis');
                if (!k) return null;
                k.scrollIntoView({block: 'start'});
                const r = k.getBoundingClientRect();
                return {top: r.top, height: r.height, visible: r.height > 0 && getComputedStyle(k).display !== 'none'};
            }""")
            print(f"  mobile kpi box: {kpi_box}")
            if kpi_box and kpi_box.get('visible'):
                await m_page.locator("#bt-kpis").first.screenshot(path=f"{ART}/10_mobile_kpis.png")
                print("  ✓ #bt-kpis → 10_mobile_kpis.png")
            else:
                # fallback: 滚动后再截
                await m_page.evaluate("document.getElementById('bt-kpis')?.scrollIntoView()")
                await m_page.wait_for_timeout(500)
                await m_page.locator("#bt-kpis").first.screenshot(path=f"{ART}/10_mobile_kpis.png", timeout=10000)
        except Exception as e:
            print(f"  mobile kpi 截图失败: {e}")

        # 月度表移动端
        try:
            await m_page.evaluate("document.getElementById('bt-monthly-host, #bt-monthly')?.scrollIntoView()")
            await m_page.wait_for_timeout(500)
            await m_page.locator("#bt-monthly-host, #bt-monthly").first.screenshot(path=f"{ART}/11_mobile_monthly.png", timeout=10000)
            print("  ✓ #bt-monthly → 11_mobile_monthly.png")
        except Exception as e:
            print(f"  mobile monthly 截图失败: {e}")

        await browser.close()
        print("\n=== R56 视觉验证完成 → ", ART)


if __name__ == "__main__":
    asyncio.run(main())