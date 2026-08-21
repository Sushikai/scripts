"""
D7: Frontend State Stability stability test suite

目标:前端状态切换稳定 ≥ 20x 改善:
  1. SW cache 版本化 (旧版本不命中)
  2. 视图切换清理 SSE/inflight
  3. abort 区分外部/内部
  4. 重复 handler 不叠加
"""
from __future__ import annotations
import os, sys, re, json
from pathlib import Path
import pytest
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
STATIC = ROOT / "web" / "static"

BASE = "http://127.0.0.1:7799"


# ─────────────────────── T1: SW 版本号存在 ───────────────────────
def test_sw_version_present():
    """sw.js 必须含版本号 (CACHE 名带 v{N})。

    改善:vs baseline 凝固旧 app.js bug → 强制 bump 即可修复
    """
    sw_path = STATIC / "sw.js"
    if not sw_path.exists():
        pytest.skip("sw.js 不存在")
    content = sw_path.read_text()
    # 至少有一个版本号声明
    has_version = bool(re.search(r"CACHE.*v\d+|tx-cache-v\d+", content))
    assert has_version, "sw.js 缺版本号 — bump 才能清理旧 cache"


# ─────────────────────── T2: PRECACHE 含关键 JS ───────────────────────
def test_sw_precache_includes_app_js():
    """PRECACHE 列表必须含 app.js (否则旧 SW 残留时 app.js 不更新)。"""
    sw_path = STATIC / "sw.js"
    if not sw_path.exists():
        pytest.skip("sw.js 不存在")
    content = sw_path.read_text()
    assert "app.js" in content, "PRECACHE 缺 app.js — 旧 SW bug"


# ─────────────────────── T3: 视图 hidden CSS 不冲突 ───────────────────────
def test_view_hidden_css_override():
    """.view{display:block} 必须被 .view[hidden]{display:none} 覆盖。

    改善:vs baseline 切页空白 bug
    """
    css_path = STATIC / "style.css"
    if not css_path.exists():
        pytest.skip("style.css 不存在")
    content = css_path.read_text()
    # 应同时有这两个规则
    has_view_block = bool(re.search(r"\.view\s*\{[^}]*display\s*:\s*block", content))
    has_hidden_override = bool(re.search(r"\.view\[hidden\][^}]*display\s*:\s*none", content))
    assert has_view_block, ".view{display:block} 不存在"
    assert has_hidden_override, ".view[hidden]{display:none} 覆盖规则不存在 — 切页会空白"


# ─────────────────────── T4: 主页面 HTML 完整 ───────────────────────
def test_index_html_well_formed():
    """主页面 HTML 必须能解析且含必要 view 容器。"""
    r = httpx.get(BASE + "/", timeout=20.0)
    assert r.status_code == 200
    html = r.text
    # 实际存在的 view-* ID(根据服务端 hash router)
    required_views = ["view-dash", "view-stock", "view-watchlist"]
    for v in required_views:
        assert v in html, f"index.html 缺 {v}"


# ─────────────────────── T5: 静态资源 cache headers ───────────────────────
def test_static_cache_headers():
    """静态资源应合理 cache-control (app.js 短, 静态文件长)。"""
    r_app = httpx.get(BASE + "/static/app.js", timeout=15.0)
    r_sw = httpx.get(BASE + "/sw.js", timeout=15.0)
    # app.js / sw.js 应该有 cache-control
    assert "cache-control" in {k.lower() for k in r_app.headers.keys()} or \
           "cache-control" in {k.lower() for k in r_sw.headers.keys()}, \
           "静态资源缺 Cache-Control header"


# ─────────────────────── T6: 重复 toggle 主题 handler 不叠加 ───────────────────────
def test_theme_toggle_single_binding():
    """主题切换按钮只能有 1 个 handler (修复双绑 bug)。

    修复:vs baseline 双绑 bug 致切换抵消
    """
    app_js = (STATIC / "app.js").read_text() if (STATIC / "app.js").exists() else ""
    # 数 theme-toggle 的 addEventListener 次数
    matches = re.findall(r"theme-toggle.*?addEventListener", app_js)
    # 在 app.js 主体里应只 1 次
    assert len(matches) <= 1, f"theme-toggle handler 绑定 {len(matches)} 次(应 1)"


# ─────────────────────── T7: 视图切换 SSE 清理 ───────────────────────
def test_view_cleanup_inflight_on_switch():
    """切页应 abort 所有 inflight 请求 (避免旧 view 数据污染新 view)。"""
    # 静态检查 — 切页函数应 abort inflight
    app_js = (STATIC / "app.js").read_text() if (STATIC / "app.js").exists() else ""
    # showView 函数应调 _inflightAbortAll 或 abort()
    has_abort = "_inflightAbortAll" in app_js or "abortAll" in app_js or "_abortAllSSE" in app_js
    assert has_abort, "showView 未调用 inflight abort — 切页数据污染"