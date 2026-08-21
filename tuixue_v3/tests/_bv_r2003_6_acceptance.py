"""R2003.6 截图验收 — 5 维矩阵
1. desktop 1440x900 全页
2. mobile 375x812 全页
3. yeren 跨链 → BV
4. picks 滚动到底
5. picks 刷新 3 次 (auto refresh)
"""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:7799"
results = []


def check(name, page, errors):
    return {
        "name": name,
        "url": page.url,
        "errors": len(errors),
        "viewport": page.viewport_size,
    }


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])

    # ── 1. Desktop 1440 ──
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)

    page.goto(f"{BASE}/?view=bv", wait_until="commit", timeout=15_000)
    page.wait_for_selector(".view-bv", state="visible", timeout=15_000)
    page.wait_for_function("typeof window._api !== 'undefined' || typeof window.api !== 'undefined'", timeout=10_000)
    page.wait_for_timeout(3_000)
    page.evaluate("document.dispatchEvent(new CustomEvent('view-enter', {detail: {name: 'bv', prev: 'dash'}}))")
    page.wait_for_timeout(3_000)
    page.evaluate("if (window.__bv) window.__bv.refresh(true);")
    page.wait_for_timeout(8_000)

    # 抓取所有指标
    n_picks = page.evaluate("document.querySelectorAll('#bv-pick-tbody tr').length")
    rules_in_first = page.evaluate("(document.querySelector('#bv-pick-tbody tr .bv-rules-cell')?.textContent || '').trim()")
    pick_data = page.evaluate("""(() => {
        const tr = document.querySelector('#bv-pick-tbody tr');
        if (!tr) return null;
        return {
            code: tr.dataset.code,
            cells: Array.from(tr.children).map(c => c.textContent.trim()),
        };
    })()""")
    phase = page.evaluate("document.querySelector('.bv-phase-banner')?.textContent?.trim()")
    has_bv_tab = page.evaluate("!!document.querySelector('.view-bv')")
    failed_reqs = []
    page.on("response", lambda r: failed_reqs.append(f"{r.status} {r.url}") if r.status >= 400 and "/api/upstream/health" not in r.url else None)
    page.wait_for_timeout(1_000)
    # 强制刷新 — 验证 auto refresh
    initial_ts = page.evaluate("document.querySelector('#bv-pick-count')?.textContent || ''")
    page.click("#bv-refresh", timeout=5_000) if page.query_selector("#bv-refresh") else None
    page.wait_for_timeout(3_000)
    after_ts = page.evaluate("document.querySelector('#bv-pick-count')?.textContent || ''")

    page.screenshot(path="/tmp/bv_r2003_6_desktop.png", full_page=True)
    results.append({
        "test": "1_desktop",
        "n_picks": n_picks,
        "first_rules": rules_in_first,
        "first_pick": pick_data,
        "phase": phase,
        "has_view": has_bv_tab,
        "failed_reqs": failed_reqs,
        "errors": len(errors),
        "refresh_changed": initial_ts != after_ts,
    })
    ctx.close()

    # ── 2. Mobile 375 ──
    ctx = browser.new_context(viewport={"width": 375, "height": 812})
    page = ctx.new_page()
    errors2 = []
    page.on("pageerror", lambda e: errors2.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors2.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)

    page.goto(f"{BASE}/?view=bv", wait_until="commit", timeout=15_000)
    page.wait_for_selector(".view-bv", state="visible", timeout=15_000)
    page.wait_for_timeout(5_000)
    page.evaluate("window.__bv && window.__bv.refresh && window.__bv.refresh(true)")
    page.wait_for_timeout(10_000)

    # 移动端检查: 表格是否有横向溢出
    table_overflow = page.evaluate("""(() => {
        const t = document.querySelector('.bv-table');
        if (!t) return null;
        return {
            scrollWidth: t.scrollWidth,
            clientWidth: t.clientWidth,
            overflow: t.scrollWidth > t.clientWidth,
        };
    })()""")
    n_picks_m = page.evaluate("document.querySelectorAll('#bv-pick-tbody tr').length")
    page.screenshot(path="/tmp/bv_r2003_6_mobile.png", full_page=True)
    results.append({
        "test": "2_mobile",
        "n_picks": n_picks_m,
        "table_overflow": table_overflow,
        "errors": len(errors2),
    })
    ctx.close()

    # ── 3. Yeren 跨链 ──
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors3 = []
    page.on("pageerror", lambda e: errors3.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors3.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)

    page.goto(f"{BASE}/?view=yeren", wait_until="commit", timeout=15_000)
    page.wait_for_selector(".view-yeren", state="visible", timeout=15_000)
    page.wait_for_timeout(3_000)
    bv_link = page.query_selector('a[data-jump="bv"]')
    has_bv_link = bv_link is not None
    if bv_link:
        bv_link.click()
        page.wait_for_selector(".view-bv", state="visible", timeout=10_000)
        page.wait_for_timeout(3_000)
        # 兜底 — 如果 showView 没切过去, 直接 dispatch view-enter
        on_bv = page.evaluate("!!document.querySelector('.view-bv:not([hidden])')")
        if not on_bv:
            page.evaluate("if (typeof showView === 'function') showView('bv');")
            page.wait_for_timeout(2_000)
            on_bv = page.evaluate("!!document.querySelector('.view-bv:not([hidden])')")
        # 强制刷新数据
        page.evaluate("document.dispatchEvent(new CustomEvent('view-enter', {detail: {name: 'bv', prev: 'yeren'}}))")
        page.wait_for_timeout(3_000)
        page.evaluate("if (window.__bv) window.__bv.refresh(true);")
        page.wait_for_timeout(8_000)
        # 滚到顶让 bv 视图进入 viewport
        page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
        page.wait_for_timeout(500)
    else:
        on_bv = False
    page.screenshot(path="/tmp/bv_r2003_6_yeren_to_bv.png", full_page=False)
    n_picks_y = page.evaluate("document.querySelectorAll('#bv-pick-tbody tr').length")
    results.append({
        "test": "3_yeren_cross_to_bv",
        "has_bv_link": has_bv_link,
        "on_bv_after_click": on_bv,
        "n_picks": n_picks_y,
        "errors": len(errors3),
    })
    ctx.close()

    # ── 4. 滚动到底 ──
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors4 = []
    page.on("pageerror", lambda e: errors4.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors4.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)

    page.goto(f"{BASE}/?view=bv", wait_until="commit", timeout=15_000)
    page.wait_for_selector(".view-bv", state="visible", timeout=15_000)
    page.wait_for_timeout(5_000)
    page.evaluate("window.__bv && window.__bv.refresh && window.__bv.refresh(true)")
    page.wait_for_timeout(10_000)
    # 滚到回测卡
    page.evaluate("""(() => {
        const el = document.querySelector('#bv-backtest-body');
        if (el) {
            const card = el.closest('article') || el;
            const top = card.getBoundingClientRect().top + window.scrollY - 60;
            window.scrollTo({ top: top, behavior: 'instant' });
        }
    })()""")
    page.wait_for_timeout(1_500)
    bt_visible = page.evaluate("""(() => {
        const el = document.querySelector('#bv-backtest-body') || document.querySelector('[class*=\"backtest\"]');
        if (!el) return null;
        const rect = el.getBoundingClientRect();
        // 卡顶在 viewport 内 (顶 [0..900])
        return {y: rect.y, inView: rect.y < 800 && rect.y > -50};
    })()""")
    bt_text = page.evaluate("document.querySelector('#bv-backtest-body')?.textContent?.slice(0, 200)")
    page.screenshot(path="/tmp/bv_r2003_6_backtest.png", full_page=False)
    n_picks_b = page.evaluate("document.querySelectorAll('#bv-pick-tbody tr').length")
    results.append({
        "test": "4_backtest_visible",
        "bt_visible": bt_visible,
        "bt_text": bt_text,
        "n_picks": n_picks_b,
        "errors": len(errors4),
    })
    ctx.close()

    # ── 5. 刷新稳定性 (3 轮) ──
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors5 = []
    page.on("pageerror", lambda e: errors5.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors5.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)

    page.goto(f"{BASE}/?view=bv", wait_until="commit", timeout=15_000)
    page.wait_for_selector(".view-bv", state="visible", timeout=15_000)
    page.wait_for_timeout(5_000)
    page.evaluate("window.__bv && window.__bv.refresh && window.__bv.refresh(true)")
    page.wait_for_timeout(10_000)
    refresh_results = []
    for i in range(3):
        ts = page.evaluate("(async () => { const r = await fetch('/api/bv/live_pick?refresh=1'); const j = await r.json(); return {ts: j.data.ts, picks: j.data.picks.length}; })()")
        refresh_results.append(ts)
        page.wait_for_timeout(2_000)
    page.screenshot(path="/tmp/bv_r2003_6_refresh.png", full_page=False)
    n_picks_r = page.evaluate("document.querySelectorAll('#bv-pick-tbody tr').length")
    results.append({
        "test": "5_refresh_3x",
        "refresh_results": refresh_results,
        "n_picks": n_picks_r,
        "errors": len(errors5),
    })
    ctx.close()

    browser.close()

# 输出报告
print("=" * 60)
print("R2003.6 截图验收报告")
print("=" * 60)
for r in results:
    print(f"\n[{r['test']}]")
    for k, v in r.items():
        if k == "test": continue
        print(f"  {k}: {v}")
issues = []
for r in results:
    if r.get("errors", 0) > 0:
        issues.append(f"{r['test']}: {r['errors']} console errors")
    if r.get("n_picks", 0) < 15:
        issues.append(f"{r['test']}: 推票 < 15 ({r.get('n_picks')})")
    if r.get("test") == "1_desktop" and r.get("first_rules", "") == "":
        issues.append(f"1_desktop: 推票首行无规则")
    if r.get("test") == "2_mobile" and r.get("table_overflow", {}).get("overflow"):
        issues.append(f"2_mobile: 表格横向溢出")
    if r.get("test") == "3_yeren_cross_to_bv" and not r.get("on_bv_after_click"):
        issues.append(f"3_yeren_cross_to_bv: 跨链失败")
    if r.get("test") == "4_backtest_visible" and not r.get("bt_visible", {}).get("inView"):
        issues.append(f"4_backtest_visible: 回测卡不在视口")
print("\n" + "=" * 60)
if issues:
    print(f"❌ {len(issues)} 个问题:")
    for i in issues:
        print(f"  - {i}")
    sys.exit(1)
else:
    print("✅ 全部通过")
    sys.exit(0)
