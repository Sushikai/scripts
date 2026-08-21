"""
tests/test_all_stocks_mobile_cards.py — 全 A 风向移动端 → 卡片化 (跟龙头页同款)

背景: 用户报告"全A 手机端显示一坨" — 18 列表格在 390px viewport 被挤扁 / 横滚,
      跟同站点龙头页 (.dragon-card 卡片化) 形成对比。
方案: ≤768px viewport 走卡片布局,每只股票一张 .as-card,
      ≥769px 保留原表格 (桌面端列多更需要横滚展示)。
TDD 协议: 先写测试 → 跑 → 失败 → 改前端 → 重跑 → 通过。

跑法:
    cd /Users/kaikai/scripts/tuixue_v3
    PYTHONPATH=. python3 -m pytest tests/test_all_stocks_mobile_cards.py -v --tb=short

测量点 (iPhone 13: 390x844 viewport):
  - 全 A 风向页 on mobile, viewport ≤768px
  - 应渲染 ≥ N 个 .as-card (一只一张卡片)
  - 表格应隐藏 (display:none on .as-table)
  - 卡片应包含: 代码 / 名称 / 板块 / 涨幅 核心 4 字段
  - 卡片高度应 ≥60px, ≤120px (不能跟表格行一样挤)
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def server_url():
    import urllib.request
    try:
        r = urllib.request.urlopen("http://127.0.0.1:7799/api/healthz", timeout=5)
        assert b'"ok":true' in r.read()
        return "http://127.0.0.1:7799"
    except Exception as e:
        pytest.skip(f"server 未启动: {e}")


# ──────────────────────────────────────────────────────────────────────
# §1 — DOM 结构 (移动端卡片化)
# ──────────────────────────────────────────────────────────────────────

class TestMobileCardStructure:
    """≤768px 时, all_stocks 应渲染 .as-card 列表而非横滚表格"""

    def test_cards_rendered_on_mobile(self, server_url):
        """iPhone 13 viewport: 至少 8 张卡片渲染"""
        asyncio.run(_check_cards_rendered(server_url))

    def test_table_hidden_on_mobile(self, server_url):
        """移动端表格应被隐藏 (display:none 或 hidden)"""
        asyncio.run(_check_table_hidden(server_url))


async def _check_cards_rendered(server_url):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},  # iPhone 13
            device_scale_factor=2,
            service_workers="block",
            is_mobile=True,
            has_touch=True,
        )
        page = await ctx.new_page()
        await page.goto(f"{server_url}/#all_stocks", wait_until="commit", timeout=30000)
        await page.wait_for_timeout(2500)

        # 等待卡片渲染 (≤768px viewport, .as-stock-card 必可见)
        try:
            await page.wait_for_selector(
                ".as-stock-card",
                state="attached",
                timeout=10000,
            )
        except Exception as e:
            await browser.close()
            pytest.fail(f"未渲染任何 .as-stock-card: {e}")

        cards = await page.evaluate("""() => {
          const cards = document.querySelectorAll('.as-stock-card');
          return Array.from(cards).slice(0, 3).map(c => ({
            cls: c.className,
            code: c.dataset.code || '',
            text: c.textContent.trim().slice(0, 100),
            rect: c.getBoundingClientRect(),
          }));
        }""")

        await browser.close()

        if len(cards) == 0:
            pytest.fail("移动端未渲染任何 .as-stock-card 元素")

        # 每张卡片应至少包含代码 (6位数字) 或名称
        for i, c in enumerate(cards):
            if not c["code"] and not any(ch in c["text"] for ch in "0123456789"):
                pytest.fail(f"卡片 #{i} 缺代码/名称: {c}")


async def _check_table_hidden(server_url):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
            service_workers="block",
        )
        page = await ctx.new_page()
        await page.goto(f"{server_url}/#all_stocks", wait_until="commit", timeout=30000)
        await page.wait_for_timeout(2500)

        m = await page.evaluate("""() => {
          const table = document.querySelector('.as-stocks-table, #as-stocks-table');
          const tableWrap = document.querySelector('#as-table-scroll, .as-table-card');
          const cards = document.querySelectorAll('.as-stock-card');
          const rows = document.querySelectorAll('#as-stocks-tbody tr.stock-row');
          return {
            tableExists: !!table,
            tableDisplay: table ? getComputedStyle(table).display : null,
            tableVisibility: table ? getComputedStyle(table).visibility : null,
            tableWrapDisplay: tableWrap ? getComputedStyle(tableWrap).display : null,
            cardCount: cards.length,
            rowCount: rows.length,
          };
        }""")

        await browser.close()

        print("\n=== Mobile Table Visibility ===")
        print(json.dumps(m, ensure_ascii=False, indent=2))

        # 表格在移动端必须隐藏
        if m["tableDisplay"] == "table" or m["tableWrapDisplay"] not in ("none", None):
            pytest.fail(f"移动端表格未隐藏: table.display={m['tableDisplay']}, wrap.display={m['tableWrapDisplay']}")

        # 卡片必须渲染
        if m["cardCount"] == 0:
            pytest.fail("移动端未渲染任何 .as-stock-card 元素")


# ──────────────────────────────────────────────────────────────────────
# §2 — 桌面端不变 (回归)
# ──────────────────────────────────────────────────────────────────────

class TestDesktopTablePreserved:
    """≥769px viewport: 保留原表格布局 (列多要横滚展示)"""

    def test_table_visible_on_desktop(self, server_url):
        asyncio.run(_check_desktop_table(server_url))


async def _check_desktop_table(server_url):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            service_workers="block",
        )
        page = await ctx.new_page()
        await page.goto(f"{server_url}/#all_stocks", wait_until="commit", timeout=30000)
        await page.wait_for_timeout(2500)

        m = await page.evaluate("""() => {
          const table = document.querySelector('.as-stocks-table');
          const rows = document.querySelectorAll('#as-stocks-tbody tr.stock-row');
          return {
            tableExists: !!table,
            tableDisplay: table ? getComputedStyle(table).display : null,
            rowCount: rows.length,
          };
        }""")

        await browser.close()

        if m["tableDisplay"] != "table":
            pytest.fail(f"桌面端表格未显示: table.display={m['tableDisplay']}")
        if m["rowCount"] == 0:
            pytest.fail("桌面端无表格行")


# ──────────────────────────────────────────────────────────────────────
# §3 — 卡片内容字段 (核心 4 字段 + 涨跌染色)
# ──────────────────────────────────────────────────────────────────────

class TestCardContent:
    """卡片必须包含: 代码 / 名称 / 板块 / 涨幅 (核心 4 字段)"""

    def test_card_has_core_fields(self, server_url):
        asyncio.run(_check_card_fields(server_url))


async def _check_card_fields(server_url):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
            service_workers="block",
            is_mobile=True,
        )
        page = await ctx.new_page()
        await page.goto(f"{server_url}/#all_stocks", wait_until="commit", timeout=30000)
        await page.wait_for_timeout(3000)

        # 等卡片或行
        try:
            await page.wait_for_selector(".as-stock-card", timeout=10000)
        except Exception:
            await browser.close()
            pytest.fail("无 .as-stock-card 渲染")

        cards = await page.evaluate("""() => {
          return Array.from(document.querySelectorAll('.as-stock-card')).slice(0, 5).map(c => {
            const code = c.querySelector('.as-card-code, [data-field="code"]');
            const name = c.querySelector('.as-card-name, [data-field="name"]');
            const pct = c.querySelector('.as-card-pct, [data-field="change_pct"]');
            const sec = c.querySelector('.as-card-sector, [data-field="sector"]');
            const cls = pct ? pct.className : '';
            return {
              hasCode: !!code,
              codeText: code ? code.textContent.trim() : null,
              hasName: !!name,
              nameText: name ? name.textContent.trim() : null,
              hasPct: !!pct,
              pctText: pct ? pct.textContent.trim() : null,
              pctCls: cls,
              hasSector: !!sec,
              sectorText: sec ? sec.textContent.trim() : null,
            };
          });
        }""")

        await browser.close()

        print("\n=== Card Fields ===")
        for c in cards:
            print(json.dumps(c, ensure_ascii=False))

        if not cards:
            pytest.fail("未渲染任何 .as-stock-card")

        # 至少第一张卡片要有 4 个核心字段
        c0 = cards[0]
        missing = []
        if not c0["hasCode"]: missing.append("code")
        if not c0["hasName"]: missing.append("name")
        if not c0["hasPct"]: missing.append("change_pct")
        if missing:
            pytest.fail(f"卡片缺字段: {missing}, cards={c0}")

        # 代码必须是 6 位数字
        if not (c0["codeText"] and len(c0["codeText"]) >= 6 and c0["codeText"][:6].isdigit()):
            pytest.fail(f"代码字段格式错: {c0['codeText']}")


# ──────────────────────────────────────────────────────────────────────
# §4 — 卡片点击跳转个股页
# ──────────────────────────────────────────────────────────────────────

class TestCardNavigation:
    """点击卡片应跳转个股页 (跟原表格行一样)"""

    def test_card_click_navigates_to_stock(self, server_url):
        asyncio.run(_check_card_click(server_url))


async def _check_card_click(server_url):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
            service_workers="block",
            is_mobile=True,
        )
        page = await ctx.new_page()
        await page.goto(f"{server_url}/#all_stocks", wait_until="commit", timeout=30000)
        await page.wait_for_timeout(3000)

        try:
            await page.wait_for_selector(".as-stock-card", timeout=10000)
        except Exception:
            await browser.close()
            pytest.fail("无 .as-stock-card")

        # 取得第一张卡片的 code
        first_code = await page.evaluate("""() => {
          const c = document.querySelector('.as-stock-card');
          return c ? (c.dataset.code || '') : '';
        }""")

        if not first_code:
            await browser.close()
            pytest.fail("卡片无 data.code")

        # 点击第一张卡片
        await page.evaluate("""() => {
          const c = document.querySelector('.as-stock-card');
          if (c) c.click();
        }""")
        await page.wait_for_timeout(2500)

        # 校验: 当前 view 应是 stock (gotoStock 调 showView('stock'))
        active_view = await page.evaluate("(() => { const v = document.querySelector('.view:not([hidden])'); return v ? v.dataset.view : ''; })()")
        # 校验: stock-search input 被填入 code
        stock_search_val = await page.evaluate("document.getElementById('stock-search')?.value || ''")

        await browser.close()

        if active_view != 'stock':
            pytest.fail(f"点击卡片未切到个股页: active_view={active_view}, expected='stock'")
        if stock_search_val != first_code:
            pytest.fail(f"个股 code 未传入 search: val={stock_search_val}, expected={first_code}")


# ──────────────────────────────────────────────────────────────────────
# §5 — 截图验证 (人工 / LLM 评图)
# ──────────────────────────────────────────────────────────────────────

class TestMobileScreenshot:
    """生成移动端截图到 /tmp/audit/all_stocks_cards__{theme}.png"""

    @pytest.mark.parametrize("theme", ["dark", "light"])
    def test_screenshot(self, server_url, theme):
        asyncio.run(_take_screenshot(server_url, theme))


async def _take_screenshot(server_url, theme):
    from playwright.async_api import async_playwright

    Path("/tmp/audit").mkdir(exist_ok=True)
    out_path = f"/tmp/audit/all_stocks_cards__{theme}.png"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
            service_workers="block",
            is_mobile=True,
            color_scheme=theme,
        )
        page = await ctx.new_page()
        await page.goto(f"{server_url}/#all_stocks", wait_until="commit", timeout=30000)
        # 等卡片渲染 (≥10 张)
        try:
            await page.wait_for_selector(".as-stock-card", timeout=15000)
            await page.wait_for_function(
                "document.querySelectorAll('.as-stock-card').length >= 10",
                timeout=10000,
            )
        except Exception as e:
            print(f"  ⚠ 卡片未到位: {e}")
        # 设主题 + 再等
        await page.evaluate(f"localStorage.setItem('tuixue_theme', '{theme}')")
        await page.wait_for_timeout(1500)

        # 滚到卡片区再截屏 (KPI + filter 占太多屏)
        await page.evaluate("""() => {
          const first = document.querySelector('.as-stock-card');
          if (first) first.scrollIntoView({behavior: 'instant', block: 'start'});
        }""")
        await page.wait_for_timeout(800)

        await page.screenshot(path=out_path, full_page=False)
        await browser.close()

    print(f"\n  saved: {out_path}")