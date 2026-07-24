"""前端端到端测试:Playwright 跨 viewport 巡检 + console 检查 + 视图渲染。"""

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


VIEWPORTS = [
    ("desktop", 1280, 800),
    ("tablet", 768, 1024),
    ("mobile", 390, 844),
]


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


def _wait_for_app(page):
    page.wait_for_selector(".topbar", timeout=5000)
    page.wait_for_selector(".drawer-link", timeout=5000)
    page.wait_for_function("typeof window.flow !== 'undefined'", timeout=5000)


def test_index_loads(browser, flow_server):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(flow_server["base"] + "/")
    _wait_for_app(page)
    assert "flow" in page.title().lower() or "视频" in page.title()
    # topbar + brand + drawer 都该在
    assert page.locator(".brand").is_visible()
    assert page.locator(".drawer-link").count() == 9  # 8 原 + Comments
    page.close()


def test_console_no_errors(browser, flow_server):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(flow_server["base"] + "/")
    _wait_for_app(page)
    page.wait_for_timeout(800)
    assert not errors, f"console errors: {errors}"
    page.close()


@pytest.mark.parametrize("vp_name,w,h", VIEWPORTS, ids=[v[0] for v in VIEWPORTS])
def test_views_render_at_viewport(browser, flow_server, vp_name, w, h):
    page = browser.new_page(viewport={"width": w, "height": h})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(flow_server["base"] + "/")
    _wait_for_app(page)
    for hash_ in ["dashboard", "new", "projects", "library", "accounts", "uploads", "logs", "settings"]:
        page.evaluate(f"window.location.hash = '#{hash_}'")
        page.wait_for_timeout(200)
        # 视图容器应该有内容
        view = page.locator(".view-host > *").first
        assert view.is_visible(), f"view {hash_} at {vp_name} not visible"
    assert not errors, f"console errors at {vp_name}: {errors}"
    page.close()


def test_theme_toggle(browser, flow_server):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(flow_server["base"] + "/")
    _wait_for_app(page)
    before = page.evaluate("document.documentElement.getAttribute('data-theme')")
    page.locator("#theme-toggle").click()
    page.wait_for_timeout(100)
    after = page.evaluate("document.documentElement.getAttribute('data-theme')")
    assert before != after
    page.close()


def test_conn_light(browser, flow_server):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(flow_server["base"] + "/")
    _wait_for_app(page)
    page.wait_for_function(
        "document.getElementById('conn-light').className.includes('ok') || document.getElementById('conn-light').className.includes('err')",
        timeout=5000,
    )
    cls = page.evaluate("document.getElementById('conn-light').className")
    assert "ok" in cls or "err" in cls
    page.close()


def test_drawer_toggle(browser, flow_server):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(flow_server["base"] + "/")
    _wait_for_app(page)
    drawer = page.locator("#drawer")
    assert "open" not in (drawer.get_attribute("class") or "")
    page.locator("#hamburger").click()
    page.wait_for_timeout(250)
    assert "open" in (drawer.get_attribute("class") or "")
    page.close()


def test_spa_navigate(browser, flow_server):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(flow_server["base"] + "/")
    _wait_for_app(page)
    page.evaluate("window.location.hash = '#library'")
    page.wait_for_function("document.querySelector('.view-host > *')?.textContent.includes('素材')", timeout=3000)
    text = page.locator(".view-host > *").first.text_content()
    assert "素材" in text
    page.close()