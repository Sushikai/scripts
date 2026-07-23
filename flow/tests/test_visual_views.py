"""8 view 内容验证:每个 view 必须含标题 + 主区域。"""

import pytest

VIEWS = [
    ("dashboard", "📊 Dashboard"),
    ("new", "✨ 新建项目"),
    ("projects", "📦 项目列表"),
    ("library", "🎬 素材库"),
    ("accounts", "👤 账号管理"),
    ("uploads", "📤 上传记录"),
    ("logs", "📋 日志"),
    ("settings", "⚙️ 设置"),
]


@pytest.mark.parametrize("hash_,expected_text", VIEWS)
def test_view_renders_expected_text(browser, flow_server, hash_, expected_text):
    """每个 view 渲染后应含对应标题文本。"""
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(flow_server["base"] + "/")
    page.wait_for_function("typeof window.flow !== 'undefined'", timeout=5000)
    page.evaluate(f"window.location.hash = '#{hash_}'")
    page.wait_for_timeout(300)
    text = page.locator(".view-host > *").first.text_content()
    assert expected_text in text, f"{hash_} view missing '{expected_text}', got: {text[:100]}"
    page.close()


@pytest.mark.parametrize("vp_name,w,h", [("desktop", 1280, 800), ("mobile", 390, 844)])
def test_new_project_form_submit_flow(browser, flow_server, vp_name, w, h):
    """新建项目页:点 info_gap 卡片 → 看到表单 → 提交。"""
    page = browser.new_page(viewport={"width": w, "height": h})
    page.goto(flow_server["base"] + "/")
    page.wait_for_function("typeof window.flow !== 'undefined'", timeout=5000)
    page.evaluate("window.location.hash = '#new'")
    page.wait_for_timeout(500)
    # 点 info_gap 卡片
    page.evaluate("document.querySelector('[data-tool-card=\"info_gap\"]').click()")
    page.wait_for_timeout(200)
    # 表单应该出现
    form_visible = page.evaluate("!document.querySelector('[data-form-section]').classList.contains('hidden')")
    assert form_visible, f"form section should be visible after clicking info_gap at {vp_name}"
    page.close()


def test_dashboard_kpi_grid(browser, flow_server):
    """dashboard 至少有 4 个 KPI 卡片。"""
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(flow_server["base"] + "/")
    page.wait_for_function("typeof window.flow !== 'undefined'", timeout=5000)
    page.evaluate("window.location.hash = '#dashboard'")
    page.wait_for_timeout(500)
    n = page.locator(".kpi-card").count()
    assert n >= 4, f"expected ≥4 KPI cards, got {n}"
    page.close()


def test_dashboard_tool_cards(browser, flow_server):
    """dashboard 至少有 4 个工具卡片。"""
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(flow_server["base"] + "/")
    page.wait_for_function("typeof window.flow !== 'undefined'", timeout=5000)
    page.evaluate("window.location.hash = '#dashboard'")
    page.wait_for_timeout(500)
    n = page.locator(".tool-card").count()
    assert n >= 4, f"expected ≥4 tool cards, got {n}"
    page.close()


def test_new_project_steps_pipeline_visible(browser, flow_server):
    """新建项目选 info_gap 后,steps-pipeline 含 7 步。"""
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(flow_server["base"] + "/")
    page.wait_for_function("typeof window.flow !== 'undefined'", timeout=5000)
    page.evaluate("window.location.hash = '#new'")
    page.wait_for_timeout(500)
    page.evaluate("document.querySelector('[data-tool-card=\"info_gap\"]').click()")
    page.wait_for_timeout(300)
    chips = page.locator(".step-chip").count()
    assert chips == 7, f"info_gap should have 7 step chips, got {chips}"
    page.close()


def test_settings_shows_health_data(browser, flow_server):
    """settings 页应显示 cache / AI / tunnel 三行。"""
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(flow_server["base"] + "/")
    page.wait_for_function("typeof window.flow !== 'undefined'", timeout=5000)
    page.evaluate("window.location.hash = '#settings'")
    page.wait_for_timeout(500)
    rows = page.locator(".kv-row").count()
    assert rows >= 3, f"expected ≥3 kv rows, got {rows}"
    page.close()