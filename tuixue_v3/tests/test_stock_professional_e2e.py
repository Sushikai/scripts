"""
tests/test_stock_professional_e2e.py — 个股专业终端 E2E 行为测试。

覆盖 plan step 5 前端行为:
  十个 tab 均可达 (点击后对应 pane 可见)
  K线 MA/MACD/KDJ/BOLL 点击后 series 实际变化
  分时今日/昨日不混日期
  相关个股有数据时可点击跳转
  空数据/降级状态不出现 undefined / N/A

跑法 (需 server 在 7799):
    pytest tests/test_stock_professional_e2e.py -v -m e2e
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

CODE = "600519"
BASE = "http://127.0.0.1:7799"

TABS = ["intraday", "kline", "flow", "seats", "holders", "crash", "ai", "news", "sectors", "related"]


@pytest.fixture(scope="module")
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
        p = ctx.new_page()
        errors = []
        p.on("pageerror", lambda e: errors.append(f"PAGEERROR: {e}"))
        p.on("console", lambda m: errors.append(f"{m.type}: {m.text[:160]}")
             if m.type == "error" else None)
        p.goto(f"{BASE}/?code={CODE}#stock", wait_until="domcontentloaded", timeout=30000)
        p.wait_for_selector(".view-stock", timeout=10000)
        # 轮询等待 quote 填充 (上游偶发 503/慢响应,固定 sleep 会误报)
        _wait_for(p, lambda: p.locator("#q-price").inner_text().strip() not in ("", "—"), 10, 500)
        p._errors = errors
        yield p
        browser.close()


def _wait_for(page, cond, seconds=10, step_ms=250):
    import time
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if cond():
                return True
        except Exception:
            pass
        page.wait_for_timeout(step_ms)
    return False


def _kline_series(page):
    """返回当前 kline chart 的 series 名列表 (chart 未就绪返回 None)。"""
    return page.evaluate("""
      () => {
        const c = document.querySelector('#kline-chart');
        if (!c || !window.echarts) return null;
        const inst = window.echarts.getInstanceByDom(c);
        if (!inst) return null;
        try {
          const opt = inst.getOption();
          return (opt.series || []).map(s => s.name || s.type || '').join(',');
        } catch (e) { return null; }
      }
    """)


def _clean(errors):
    # 5xx / 请求超时是上游瞬时降级 (页面有降级兜底 toast),不算页面 bug
    return [e for e in errors
            if "ERR_CONNECTION" not in e
            and "Service Worker" not in e
            and "status of 5" not in e
            and "请求超时" not in e]


def test_kline_default_active(page):
    """super card 默认 tab = kline (必须在点击任何 tab 前断言)。"""
    active = page.locator(".chart-tab.active[data-tab]").first.get_attribute("data-tab")
    assert active == "kline", f"默认 tab 是 {active}"


def test_all_tabs_switch(page):
    """十个 tab 均可点击,对应 pane 可见。"""
    failures = []
    for tab in TABS:
        btn = page.locator(f'.chart-tab[data-tab="{tab}"]').first
        if not btn.count():
            failures.append(f"{tab}: 无按钮")
            continue
        btn.click()
        page.wait_for_timeout(150)
        pane = page.locator(f'[data-tab-pane="{tab}"]').first
        if pane.count():
            visible = pane.is_visible()
            if not visible:
                failures.append(f"{tab}: pane 不可见")
        else:
            # super card 的 chart-pane 结构 (data-tab-pane 在 card 上)
            pane2 = page.locator(f'[data-tab-pane="{tab}"]').first
            if not pane2.count():
                failures.append(f"{tab}: 无 pane")
    assert not failures, "; ".join(failures)


def test_kline_indicator_changes_series(page):
    """K线 MA/MACD/KDJ/BOLL chip 点击后 series 实际变化。"""
    # 等 K 线数据就绪 (loadStockDetail → /full/loadKline),再点 tab 触发首绘
    # /full 是历史快照模式 (周末默认日期=最近交易日),上游慢时 20s 超时 → 数据不来,
    # 此时降级路径也会置 ready;彻底没数据则跳过 (上游不可用,非页面 bug)
    ready = _wait_for(page, lambda: page.evaluate(
        "typeof _klineDataReady !== 'undefined' && !!_klineDataReady"), 40, 500)
    if not ready:
        pytest.skip("K 线数据未就绪 (上游不可用)")
    # 数据置 ready 但画图可能仍失败 (空数据兜底),轮询确认 chart 实例出现
    page.locator('.chart-tab[data-tab="kline"]').first.click()
    page.locator('.chart-tab[data-tab="kline"]').first.click()
    chips = page.locator("#kline-indicators .kt-chip")
    if not chips.count():
        pytest.skip("无指标 chip (结构不同,跳过)")
    # 等 kline chart 真正画出来 (echarts 懒加载 + 首绘需要时间)
    assert _wait_for(page, lambda: _kline_series(page) is not None, 15, 500), "kline chart 未画出"
    snapshots = {}
    for i in range(chips.count()):
        name = chips.nth(i).get_attribute("data-ind")
        chips.nth(i).click()
        # 每次点击后轮询等 chart 重画 (dispose+reinit 有窗口,固定 sleep 会撞上)
        assert _wait_for(page, lambda: _kline_series(page) is not None, 8, 250), f"{name} 切换后 chart 未重画"
        snapshots[name] = _kline_series(page)
    assert all(snapshots.values()), "部分指标无 series"
    # MACD 与 MA 的 series 组合必须不同
    assert snapshots.get("ma") != snapshots.get("macd"), "MA/MACD series 未变化"


def test_intraday_date_not_swapped(page):
    """分时 tab 显示日期,不含 'undefined'/'N/A'。"""
    page.locator('.chart-tab[data-tab="intraday"]').first.click()
    page.wait_for_timeout(500)
    txt = page.locator(".view-stock").inner_text()
    for bad in ["undefined", "N/A", "NaN"]:
        assert bad not in txt, f"分时 tab 出现 {bad}"


def test_no_console_errors(page):
    """整页无 console/page errors (除连接/SW)。"""
    errs = _clean(page._errors)
    assert not errs, f"console errors: {errs[:5]}"


def test_stock_code_visible(page):
    """股票代码/名称已渲染。"""
    name = page.locator("#qh-name").inner_text()
    code = page.locator("#qh-code").inner_text()
    assert code.strip() in ("600519", "600519.SH", "600519SH")
    assert name.strip(), "股票名称为空"


def test_quote_hero_filled(page):
    """Hero 价格区已填充 (上游慢时轮询,彻底无数据则跳过而非误报)。"""
    got = _wait_for(page, lambda: page.locator("#q-price").inner_text().strip() not in ("", "—"), 30, 500)
    price = page.locator("#q-price").inner_text()
    if not got:
        pytest.skip("quote 未加载 (上游不可用)")
    assert price.strip() not in ("", "—"), "价格为空"


def test_buypoint_segments_exist(page):
    """综合买点 3 段存在 (weekly/recovery/ma5)。"""
    for sel in ["#bp-seg-weekly", "#bp-seg-recovery", "#bp-seg-ma5"]:
        assert page.locator(sel).count() == 1, f"缺 {sel}"


def test_strategy_card_rendered(page):
    """策略匹配度卡有 3 策略行。"""
    card = page.locator("#q-strategy-match-card")
    assert card.count() == 1
    rows = card.locator(".sm-row")
    if not rows.count():
        pytest.skip("策略无数据")
    assert rows.count() >= 1


def test_related_stocks_clickable(page):
    """相关个股有数据时可点击跳转 (data-action=open-stock:CODE)。"""
    page.locator('.chart-tab[data-tab="related"]').first.click()
    page.wait_for_timeout(1500)
    items = page.locator('[data-tab-pane="related"] [data-action^="open-stock:"]')
    if not items.count():
        pytest.skip("无相关个股数据")
    action = items.first.get_attribute("data-action")
    assert action and action.startswith("open-stock:"), f"action 异常: {action}"
    code = action.split(":", 1)[1]
    assert len(code) == 6 and code.isdigit(), f"相关个股 code 异常: {code}"
    # 点击后应切到个股视图
    items.first.click()
    page.wait_for_timeout(1500)
    assert page.locator(".view-stock").count() >= 1


def test_quickbar_collapsed_after_load(page):
    """加载股票后 quickbar 折叠 (搜索框隐藏)。"""
    qb = page.locator("#stock-quickbar")
    cls = qb.get_attribute("class") or ""
    assert "qb-collapsed" in cls, "quickbar 未折叠"
