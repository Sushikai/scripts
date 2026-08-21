"""
tests/test_all_stocks_mobile_v250.py — 全 A 风向移动端 v250 ship

目标: 修复"全 A 移动端 25 列横滚挤 4 列"的硬伤
方案:
  - ≤768px viewport → 卡片化 (仿龙头页 .dragon-card)
  - ≥769px viewport → 保留 25 列横滚表格
  - FILTERS row 折叠成 1 行 (点"筛选"展开)
  - KPI "统计覆盖 5531" 紧凑化

TDD: 先写测试 → 跑 → 失败 (6/6) → 改前端 → 重跑 → 通过

跑法:
    cd /Users/kaikai/scripts/tuixue_v3
    PYTHONPATH=. python3 -m pytest tests/test_all_stocks_mobile_v250.py -v --tb=short
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest


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
# §1 — 卡片存在 (移动端)
# ──────────────────────────────────────────────────────────────────────

class TestMobileCardStructure:
    """≤768px 时, all_stocks 应渲染 .as-card 列表而非横滚表格"""

    def test_cards_rendered_on_iphone13(self, server_url):
        """iPhone 13 viewport: 至少 8 张卡片渲染"""
        asyncio.run(_check_cards_rendered(server_url))

    def test_table_hidden_on_iphone13(self, server_url):
        """移动端表格应被隐藏 (display:none 或 hidden)"""
        asyncio.run(_check_table_hidden(server_url))


async def _check_cards_rendered(server_url):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
            service_workers="block",
            is_mobile=True, has_touch=True,
        )
        page = await ctx.new_page()
        await page.goto(f"{server_url}/#all_stocks", wait_until="commit", timeout=30000)
        await page.wait_for_timeout(4500)
        try:
            await page.wait_for_selector(".as-stock-card", timeout=8000)
            cnt = await page.evaluate("document.querySelectorAll('.as-stock-card').length")
            assert cnt >= 8, f"cards {cnt} < 8"
        except Exception as e:
            cnt = await page.evaluate("document.querySelectorAll('.as-stock-card').length")
            await page.screenshot(path="/tmp/v250-iphone13-fail.png", full_page=False)
            raise AssertionError(f"等待 .as-stock-card 失败 (cnt={cnt}): {e}")
        await browser.close()


async def _check_table_hidden(server_url):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            service_workers="block",
            is_mobile=True, has_touch=True,
        )
        page = await ctx.new_page()
        await page.goto(f"{server_url}/#all_stocks", wait_until="commit", timeout=30000)
        await page.wait_for_timeout(3000)
        info = await page.evaluate("""(() => {
          const tbl = document.querySelector('#as-stocks-table');
          const wrap = document.querySelector('#as-table-scroll');
          const cs1 = tbl ? getComputedStyle(tbl) : null;
          const cs2 = wrap ? getComputedStyle(wrap) : null;
          return {
            tbl_display: cs1 ? cs1.display : '?',
            wrap_display: cs2 ? cs2.display : '?',
          };
        })()""")
        assert info["tbl_display"] == "none" or info["wrap_display"] == "none", \
            f"移动端表格未隐藏: {info}"
        await browser.close()


# ──────────────────────────────────────────────────────────────────────
# §2 — 卡片字段齐全
# ──────────────────────────────────────────────────────────────────────

class TestMobileCardFields:

    def test_card_has_code_name_pct_sector(self, server_url):
        """卡片应有: 代码 / 名称 / 涨幅 / 板块 / 自选按钮"""
        asyncio.run(_check_card_fields(server_url))


async def _check_card_fields(server_url):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            service_workers="block",
            is_mobile=True, has_touch=True,
        )
        page = await ctx.new_page()
        await page.goto(f"{server_url}/#all_stocks", wait_until="commit", timeout=30000)
        await page.wait_for_timeout(4500)
        await page.wait_for_selector(".as-stock-card", timeout=10000)
        info = await page.evaluate("""(() => {
          const c = document.querySelector('.as-stock-card');
          if (!c) return null;
          return {
            has_code: !!c.querySelector('.as-card-code, [class*=code]'),
            has_name: !!c.querySelector('.as-card-name, [class*=name]'),
            has_pct:  !!c.querySelector('.as-card-pct, [class*=pct]'),
            has_sector: !!c.querySelector('.as-card-sector, .chip-domain, [class*=sector]'),
            has_star: !!c.querySelector('.star-btn, [class*=star]'),
            cardH: c.getBoundingClientRect().height,
          };
        })()""")
        assert info is not None, "未找到 .as-stock-card"
        assert info["has_code"], f"卡片缺代码: {info}"
        assert info["has_name"], f"卡片缺名称: {info}"
        assert info["has_pct"], f"卡片缺涨幅: {info}"
        assert info["has_sector"], f"卡片缺板块: {info}"
        assert info["has_star"], f"卡片缺自选: {info}"
        assert 60 <= info["cardH"] <= 200, f"卡片高度异常: {info}"
        await browser.close()


# ──────────────────────────────────────────────────────────────────────
# §3 — 卡片点击跳转
# ──────────────────────────────────────────────────────────────────────

class TestMobileCardInteraction:

    def test_card_click_goes_to_stock(self, server_url):
        """点击卡片应触发跳转 (location.hash 变化或调用 stock loader)"""
        asyncio.run(_check_card_click(server_url))


async def _check_card_click(server_url):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            service_workers="block",
            is_mobile=True, has_touch=True,
        )
        page = await ctx.new_page()
        await page.goto(f"{server_url}/#all_stocks", wait_until="commit", timeout=30000)
        await page.wait_for_timeout(4500)
        await page.wait_for_selector(".as-stock-card", timeout=10000)
        # 抓第一个卡片的 code
        first = await page.evaluate("""(() => {
          const c = document.querySelector('.as-stock-card');
          return c ? (c.dataset.code || c.getAttribute('data-code') || '') : '';
        })()""")
        assert first, "卡片未带 data-code"
        # 点击 → 应触发跳转
        await page.evaluate("document.querySelector('.as-stock-card').click()")
        await page.wait_for_timeout(1500)
        new_hash = await page.evaluate("location.hash")
        # 应切到 #stock/CODE 或类似
        assert "stock" in new_hash.lower() or first in new_hash, \
            f"点击卡片未跳转: hash={new_hash} code={first}"
        await browser.close()


# ──────────────────────────────────────────────────────────────────────
# §4 — 桌面回归 (表格保留, 卡片隐藏)
# ──────────────────────────────────────────────────────────────────────

class TestDesktopRegression:

    def test_desktop_table_still_visible(self, server_url):
        """1280px 桌面: 表格仍可见, 卡片隐藏"""
        asyncio.run(_check_desktop(server_url))


async def _check_desktop(server_url):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            service_workers="block",
        )
        page = await ctx.new_page()
        await page.goto(f"{server_url}/#all_stocks", wait_until="commit", timeout=30000)
        # 等数据填进表格 (rows 累计,直到 >= 8)
        try:
            await page.wait_for_function(
                "document.querySelectorAll('#as-stocks-table tbody tr').length >= 8",
                timeout=15000,
            )
        except Exception:
            pass
        await page.wait_for_timeout(1500)
        info = await page.evaluate("""(() => {
          const tbl = document.querySelector('#as-stocks-table');
          const cardList = document.querySelector('#as-cards-list');
          const visibleCards = cardList
            ? Array.from(cardList.querySelectorAll('.as-stock-card')).filter(c => {
                const r = c.getBoundingClientRect();
                return r.width > 0 && getComputedStyle(c).display !== 'none';
              }).length
            : 0;
          const cs = tbl ? getComputedStyle(tbl) : null;
          return {
            tbl_display: cs ? cs.display : '?',
            visibleCards,
            rows: tbl ? tbl.querySelectorAll('tbody tr').length : 0,
          };
        })()""")
        assert info["tbl_display"] != "none", f"桌面表格被隐藏: {info}"
        assert info["visibleCards"] == 0, f"桌面应隐藏卡片 (visible={info['visibleCards']}): {info}"
        assert info["rows"] >= 8, f"桌面行数过少: {info}"
        await browser.close()


# ──────────────────────────────────────────────────────────────────────
# §5 — FILTERS 行折叠 (移动端)
# ──────────────────────────────────────────────────────────────────────

class TestFiltersCollapse:

    def test_filters_row_collapsible_on_mobile(self, server_url):
        """移动端 FILTERS row 默认应隐藏/紧凑, 有展开按钮"""
        asyncio.run(_check_filters(server_url))


async def _check_filters(server_url):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            service_workers="block",
            is_mobile=True, has_touch=True,
        )
        page = await ctx.new_page()
        await page.goto(f"{server_url}/#all_stocks", wait_until="commit", timeout=30000)
        await page.wait_for_timeout(3500)
        info = await page.evaluate("""(() => {
          const toggle = document.getElementById('as-filter-toggle');
          const body = document.getElementById('as-filter-body');
          const hint = document.getElementById('as-filter-hint');
          return {
            has_toggle: !!toggle,
            toggle_visible: toggle ? getComputedStyle(toggle).display !== 'none' : false,
            body_visible: body ? getComputedStyle(body).display !== 'none' : false,
            hint_visible: hint ? getComputedStyle(hint).display !== 'none' : false,
          };
        })()""")
        assert info["has_toggle"], f"无 FILTERS toggle 按钮: {info}"
        assert info["toggle_visible"], f"移动端 toggle 应可见: {info}"
        assert not info["body_visible"], f"移动端 body 应隐藏: {info}"
        assert info["hint_visible"], f"移动端 hint 应可见: {info}"
        await browser.close()