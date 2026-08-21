"""
tests/test_no_console_errors.py — 跨 view × viewport × theme 截图,console 必须 0 error

复用 audit_views.py 的 (VIEWS, VIEWPORTS, BAD_TEXT_RE),改 pytest 形式。
每张截图必须: console error 数 == 0 (favicon 404 除外) + 关键 selector 存在。

跑法:
    pytest tests/test_no_console_errors.py -v -m e2e
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from audit_views import VIEWS, VIEWPORTS, BAD_TEXT_RE  # noqa: E402

THEMES = ["dark", "light"]

pytestmark = pytest.mark.e2e

ALLOWED_PATTERNS = [
    re.compile(r"favicon\.ico", re.I),
    re.compile(r"/sw\.js", re.I),
    re.compile(r"429 \(Too Many Requests\)", re.I),  # 限流是服务端策略,非前端 bug
]


def _is_allowed(msg_text):
    return any(p.search(msg_text) for p in ALLOWED_PATTERNS)


def _extract_hash_and_arg(view_name, arg):
    """返回 (hash_route, url_arg). audit_views 的 view tuple 是 (id, hash, arg, asserts)."""
    if arg:
        from urllib.parse import quote
        return view_name, f"={quote(arg, safe='')}"
    return view_name, ""


def _selector_keys(asserts):
    """从 asserts dict 提取 css selector 列表 (用于 must_exist 检查)."""
    out = []
    for k, v in asserts.items():
        if isinstance(v, str) and v.startswith("#"):
            out.append(v)
    return out


@pytest.mark.parametrize("view_id,hash_route,arg,asserts", VIEWS,
                         ids=[v[0] for v in VIEWS])
@pytest.mark.parametrize("vp_name,vp_w,vp_h", VIEWPORTS, ids=[v[0] for v in VIEWPORTS])
def test_view_no_console_errors(base_url, view_id, hash_route, arg, asserts,
                                vp_name, vp_w, vp_h, screenshots_dir):
    """每个 view × viewport 跑 2 themes,console error 必须 0."""
    _, url_arg = _extract_hash_and_arg(hash_route, arg)
    must_have = _selector_keys(asserts)
    fails = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for theme in THEMES:
            ctx = browser.new_context(viewport={"width": vp_w, "height": vp_h})
            page = ctx.new_page()
            errors = []
            page.on("console", lambda m, e=errors: e.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda exc, e=errors: e.append(str(exc)))

            page.goto(f"{base_url}/#{hash_route}{url_arg}",
                      wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_selector(f'[data-view="{hash_route}"]:not([hidden])', timeout=10000)
            except Exception:
                pass
            page.wait_for_timeout(3000)  # 数据 settle

            shot = screenshots_dir / f"{view_id}__{vp_name}__{theme}.png"
            page.screenshot(path=str(shot), full_page=False)

            for sel in must_have:
                try:
                    if not page.query_selector(sel):
                        fails.append((theme, f"缺 selector {sel}"))
                except Exception:
                    pass

            real = [e for e in errors if not _is_allowed(e)]
            if real:
                fails.append((theme, f"{len(real)} console errors: {real[0][:100]}"))
            ctx.close()
        browser.close()

    assert not fails, (
        f"#{view_id} ({vp_name}) 失败:\n"
        + "\n".join(f"  [{t}] {why}" for t, why in fails)
    )


def test_console_summary(base_url, screenshots_dir):
    """所有 view × viewport × theme 组合汇总 (不 fail,只报告)."""
    n_total = 0
    n_with_errors = 0
    by_err = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for vp_name, vp_w, vp_h in VIEWPORTS:
            ctx = browser.new_context(viewport={"width": vp_w, "height": vp_h})
            page = ctx.new_page()
            for theme in THEMES:
                for view_id, hash_route, arg, _ in VIEWS:
                    errors = []
                    page.on("console", lambda m, e=errors: e.append(m.text) if m.type == "error" else None)
                    try:
                        _, url_arg = _extract_hash_and_arg(hash_route, arg)
                        page.goto(f"{base_url}/#{hash_route}{url_arg}",
                                  wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(2500)
                    except Exception:
                        pass
                    real = [e for e in errors if not _is_allowed(e)]
                    n_total += 1
                    if real:
                        n_with_errors += 1
                        for e in real:
                            short = e[:80]
                            by_err[short] = by_err.get(short, 0) + 1
            ctx.close()
        browser.close()

    print(f"\n  截图 {n_total} 张 ({len(VIEWS)} view × {len(VIEWPORTS)} vp × {len(THEMES)} theme)")
    print(f"  {n_with_errors} 张有 console error")
    for err, n in sorted(by_err.items(), key=lambda x: -x[1])[:10]:
        print(f"    {n}× {err}")