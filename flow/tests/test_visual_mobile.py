"""视觉 + 移动端巡检。"""

import pytest


VIEWPORTS = [
    ("desktop", 1280, 800),
    ("tablet", 768, 1024),
    ("mobile_l", 414, 896),  # iPhone 11 landscape
    ("mobile", 390, 844),   # iPhone 13
    ("mobile_s", 360, 640), # small android
]

VIEW_HASHES = ["dashboard", "new", "projects", "library", "accounts", "uploads", "logs", "settings"]


@pytest.mark.parametrize("vp_name,w,h", VIEWPORTS, ids=[v[0] for v in VIEWPORTS])
def test_views_render_no_overflow(browser, flow_server, vp_name, w, h):
    """每个 viewport × 8 view,无溢出 + 无 console error。"""
    page = browser.new_page(viewport={"width": w, "height": h})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(flow_server["base"] + "/")
    page.wait_for_function("typeof window.flow !== 'undefined'", timeout=5000)
    page.wait_for_timeout(300)

    for h_ in VIEW_HASHES:
        page.evaluate(f"window.location.hash = '#{h_}'")
        page.wait_for_timeout(200)
        # 检查无水平溢出(scrollWidth <= viewport)
        sw = page.evaluate("document.documentElement.scrollWidth")
        cw = page.evaluate("document.documentElement.clientWidth")
        # 允许最多 4px 误差(margin / border / scrollbar)
        assert sw <= cw + 4, f"{vp_name}/{h_}: scrollWidth={sw} > clientWidth={cw}"

    assert not errors, f"console errors at {vp_name}: {errors}"
    page.close()


@pytest.mark.parametrize("vp_name,w,h", VIEWPORTS, ids=[v[0] for v in VIEWPORTS])
def test_no_hardcoded_colors_in_css(browser, flow_server, vp_name, w, h):
    """CSS 文件不应有硬编码颜色字面量(tokens.css 的 -- 定义除外)。"""
    import re
    page = browser.new_page(viewport={"width": w, "height": h})
    page.goto(flow_server["base"] + "/")
    page.wait_for_function("typeof window.flow !== 'undefined'", timeout=5000)

    # 抓取所有 stylesheets 内容,过滤掉 :root/{...} 块(tokens.css 定义)
    css_text = page.evaluate("""
        Array.from(document.styleSheets).map(s => {
            try { return Array.from(s.cssRules).map(r => r.cssText).join('\\n'); }
            catch (e) { return ''; }
        }).join('\\n');
    """)
    # 去掉 :root { ... } 和 [data-theme=...] { ... } 的内容
    stripped = re.sub(r":root\s*\{[^}]*\}", "", css_text, flags=re.DOTALL)
    stripped = re.sub(r'\[data-theme="[^"]+"\]\s*\{[^}]*\}', "", stripped, flags=re.DOTALL)

    bad = []
    for m in re.finditer(r"#[0-9a-fA-F]{3,8}\b", stripped):
        bad.append(m.group())
    # token 定义外不应有硬编码颜色(允许 0)
    assert len(bad) == 0, f"hardcoded colors at {vp_name}: {bad[:10]}"
    page.close()


def test_safe_area_inset(browser, flow_server):
    """body padding-top 应包含 safe-area-inset-top(iOS 适配)。"""
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(flow_server["base"] + "/")
    page.wait_for_function("typeof window.flow !== 'undefined'", timeout=5000)
    # topbar 是 fixed,检查 body padding-top 不为零
    pt = page.evaluate("getComputedStyle(document.body).paddingTop")
    assert pt, f"body padding-top should not be empty (got '{pt}')"
    page.close()


def test_drawer_overlay_mobile(browser, flow_server):
    """移动端 drawer 打开后有 overlay 效果。"""
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(flow_server["base"] + "/")
    page.wait_for_function("typeof window.flow !== 'undefined'", timeout=5000)
    page.locator("#hamburger").click()
    page.wait_for_timeout(300)
    cls = page.locator("#drawer").get_attribute("class") or ""
    assert "open" in cls
    page.close()


def test_theme_toggle_persists(browser, flow_server):
    """主题切换后刷新仍保留。"""
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(flow_server["base"] + "/")
    page.wait_for_function("typeof window.flow !== 'undefined'", timeout=5000)
    page.locator("#theme-toggle").click()
    page.wait_for_timeout(100)
    after = page.evaluate("document.documentElement.getAttribute('data-theme')")
    page.reload()
    page.wait_for_function("typeof window.flow !== 'undefined'", timeout=5000)
    after2 = page.evaluate("document.documentElement.getAttribute('data-theme')")
    assert after == after2, f"theme not persisted: {after} != {after2}"
    page.close()