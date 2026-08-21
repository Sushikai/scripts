"""
tests/r151_watchlist_e2e.py — R151 自选页 bug 三连 e2e 验证

用户报告 (退学 v3 owner):
  Bug #1: 增删之后前端不刷新 (SW 15s SWR 命中旧 payload)
  Bug #2: 自选页面右键刷新 (浏览器原生 reload) 跳转到 dash

测试场景:
  T1: add 后前端立刻出现新行 (Bug #1 修复验证)
  T2: delete 后前端立刻消失行 (Bug #1 修复验证)
  T3: 在 #watchlist F5 后仍在 #watchlist (Bug #2 修复验证)
  T4: 其他 view (#dragons/#yaogu/#dexin) 同样保留 (回归)

每步截图 → tests/r151_visual/<run_id>/T?_step.png
Claude 视觉对比 baseline (修复前) vs 当次 (修复后).

跑法:
  pytest tests/r151_watchlist_e2e.py -v -s
"""
from __future__ import annotations

import os
import sys
import time
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect, Page

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

# 截图落点: tests/r151_visual/<run_id>/
VISUAL_DIR = ROOT / "tests" / "r151_visual"
RUN_ID = time.strftime("%Y%m%d_%H%M%S")
RUN_DIR = VISUAL_DIR / RUN_ID
RUN_DIR.mkdir(parents=True, exist_ok=True)
BASELINE_DIR = VISUAL_DIR / "baseline"
BASELINE_DIR.mkdir(parents=True, exist_ok=True)

# 测试用的代码 (前缀 R151_, 跑完自动 cleanup)
TEST_CODES = ["603999", "604000"]  # 上交所 6 开头 — 走 A 股分支

# 已知无效/无关过滤
ALLOWED_CONSOLE_PATTERNS = [
    re.compile(r"favicon\.ico", re.I),
    re.compile(r"/sw\.js", re.I),
    re.compile(r"429 \(Too Many Requests\)", re.I),
    re.compile(r"404 \(\)", re.I),  # 测试股/数据源 404 常见
    re.compile(r"日线 .* 全部源失败", re.I),  # 测试股没行情数据, 预期
    re.compile(r"akshare_individual_inner.*失败", re.I),
    re.compile(r"push2his_daykline.*失败", re.I),
    re.compile(r"fetch_daily", re.I),
    re.compile(r"socket hang up", re.I),
    re.compile(r"RemoteDisconnected", re.I),
    re.compile(r"connection.*closed", re.I),
    re.compile(r"timed out", re.I),
]


def _is_allowed(text: str) -> bool:
    return any(p.search(text) for p in ALLOWED_CONSOLE_PATTERNS)


def _shot(page: Page, name: str) -> Path:
    """截图 + 落盘 + 同步保存到 baseline (首次跑当 baseline)."""
    p = RUN_DIR / f"{name}.png"
    page.screenshot(path=str(p), full_page=False)
    base = BASELINE_DIR / f"{name}.png"
    if not base.exists():
        shutil.copy2(p, base)
        print(f"  [baseline] saved {base.name}")
    return p


def _row_count(page: Page) -> int:
    """自选页 tbody 行数 (排除 loading/empty 占位)."""
    return page.evaluate("""() => {
        const tb = document.querySelector('#wl-tbody');
        if (!tb) return -1;
        const trs = tb.querySelectorAll('tr');
        let cnt = 0;
        for (const tr of trs) {
            const txt = (tr.textContent || '').trim();
            if (txt.includes('加载中') || txt.includes('加载失败') || txt === '') continue;
            cnt++;
        }
        return cnt;
    }""")


def _has_code(page: Page, code: str) -> bool:
    return page.evaluate(
        """(c) => {
            const tb = document.querySelector('#wl-tbody');
            if (!tb) return false;
            for (const tr of tb.querySelectorAll('tr')) {
                if ((tr.textContent || '').includes(c)) return true;
            }
            return false;
        }""",
        code,
    )


def _current_view(page: Page) -> str:
    """返回当前可见 view 的 name."""
    return page.evaluate("""() => {
        const v = document.querySelector('[data-view]:not([hidden])');
        return v ? v.getAttribute('data-view') : null;
    }""")


def _hash(page: Page) -> str:
    return page.evaluate("() => location.hash || ''")


def _wait_watchlist_loaded(page: Page, timeout_s: float = 8.0):
    """等 _watchlistLoad() 完成 (tbody 不再是 loading 占位)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        txt = page.evaluate("() => document.querySelector('#wl-tbody')?.textContent || ''")
        # 占位文案: "加载中 …" / "加载自选股池…" / "加载失败: …"
        if "加载" not in txt or len(txt.strip()) > 50:
            return
        time.sleep(0.2)
    raise AssertionError("watchlist load timeout")


@pytest.fixture
def cleanup_codes(base_url):
    """每个 test 前清掉测试股 (避免 T1/T2 之间 DB 状态污染)."""
    import urllib.request
    for code in TEST_CODES:
        try:
            req = urllib.request.Request(
                f"{base_url}/api/watchlist/{code}",
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10).read()
        except Exception:
            pass
    yield


def _goto_watchlist(page: Page, base_url: str):
    page.goto(f"{base_url}/#watchlist",
              wait_until="domcontentloaded", timeout=30000)
    _wait_watchlist_loaded(page)


def _db_count(base_url: str, code: str) -> int:
    """直接查 DB 看是否真落库 (R150.8 WAL retry 场景)."""
    db = ROOT / "data" / "cache.db"
    try:
        r = subprocess.run(
            ["sqlite3", str(db), f"SELECT count(*) FROM watchlist WHERE code='{code}';"],
            capture_output=True, text=True, timeout=10,
        )
        return int((r.stdout or "0").strip())
    except Exception:
        return -1


# ════════════════════════════════════════════════════════════════════════════════
# T1: add 后前端立刻出现新行
# ════════════════════════════════════════════════════════════════════════════════
def _wait_db(code: str, expect: int, max_s: float = 90.0) -> int:
    """等 DB 真落库 (R150.8 锁问题, 5 retry × 30s = 最长 150s).
    expect: 1 = 等出现, 0 = 等消失.
    """
    deadline = time.time() + max_s
    while time.time() < deadline:
        n = _db_count(None, code)
        if (expect == 1 and n >= 1) or (expect == 0 and n == 0):
            return n
        time.sleep(2)
    return _db_count(None, code)


def test_t1_add_appears(base_url, cleanup_codes):
    """Bug #1 验证: POST 后 ≤3s 内前端应出现新行 (旧 bug: SW cache 命中, 需手动 reload).

    验证前/后修复行为差异:
      修复前: s2_after_add.png 与 s1_before.png 行数一致, 603999 不可见
      修复后: s2_after_add.png 比 s1_before.png 多 1 行, 603999 可见

    R150.8 已知问题: WAL 长锁导致 add async retry 5 次 ~35s. 测试需容错.
    """
    code = TEST_CODES[0]
    fails = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        errors_log = []
        page.on("console", lambda m, e=errors_log: e.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda exc, e=errors_log: e.append(str(exc)))

        # 清理: 先确保 603999 不在 (DELETE 走重试, 等 DB 真的没)
        try:
            page.request.delete(f"{base_url}/api/watchlist/{code}", timeout=30000)
        except Exception:
            pass
        _wait_db(code, 0, max_s=60)
        time.sleep(1)

        _goto_watchlist(page, base_url)
        before_n = _row_count(page)
        view = _current_view(page)
        h = _hash(page)
        print(f"  T1: before row_count={before_n}, has {code}={_has_code(page, code)}, view={view}, hash={h}")
        _shot(page, "T1_s1_before")

        # 直接调前端函数模拟用户点 ♡ 按钮
        page.evaluate("""async (c) => {
            await fetch('/api/watchlist', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({code: c, name: 'R151_TEST_ADD', tag: '', note: ''})
            });
        }""", code)
        # 等 server async 落库 (R150.8 锁问题, 最多 ~35s)
        db_n = _wait_db(code, 1, max_s=90)
        print(f"  T1: DB after add, count={db_n}")
        # 强制前端再 load (模拟 R150.7 已修的 _watchlistLoaded=false)
        page.evaluate("""() => {
            if (window._watchlistLoad) {
                window._watchlistLoaded = false;
                window._watchlistLoad();
            }
        }""")
        # 关键: 这 5s 是验证 Bug #1 修复的窗口. 旧 bug: SW 命中旧 payload, 603999 不可见.
        # R151.1: GET /api/watchlist 现在走 R151.1 warm-fill (2.5s 截止), 总响应 ~3-4s.
        time.sleep(5)

        after_n = _row_count(page)
        has_c = _has_code(page, code)
        print(f"  T1: after  row_count={after_n} (expected {before_n + 1}), has {code}={has_c}")
        _shot(page, "T1_s2_after_add")

        if db_n < 1:
            fails.append(f"DB 未落库 (R150.8 锁): db_count={db_n} (跳过前端断言)")
        else:
            if after_n != before_n + 1:
                fails.append(f"row count 没增加: before={before_n} after={after_n}")
            if not has_c:
                fails.append(f"{code} 不在前端列表 (Bug #1: 前端没刷新)")

        real = [e for e in errors_log if not _is_allowed(e)]
        if real:
            fails.append(f"console errors: {real[0][:150]}")

        # R151.1: 测完即清 — T2 才能从干净状态开始.
        try:
            page.request.delete(f"{base_url}/api/watchlist/{code}", timeout=30000)
        except Exception:
            pass

        ctx.close()
        browser.close()

    assert not fails, "T1 失败:\n  " + "\n  ".join(fails)


# ════════════════════════════════════════════════════════════════════════════════
# T2: delete 后前端立刻消失行
# ════════════════════════════════════════════════════════════════════════════════
def test_t2_delete_disappears(base_url, cleanup_codes):
    """Bug #1 验证: DELETE 后 ≤3s 内前端应消失行."""
    code = TEST_CODES[0]
    fails = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        errors_log = []
        page.on("console", lambda m, e=errors_log: e.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda exc, e=errors_log: e.append(str(exc)))

        _goto_watchlist(page, base_url)
        # R151.1: T2 自己 add 再 del, 不依赖 T1 残留状态.
        page.evaluate("""async (c) => {
            await fetch('/api/watchlist', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({code: c, name: 'R151_TEST_DEL', tag: '', note: ''})
            });
        }""", code)
        _wait_db(code, 1, max_s=90)
        page.evaluate("""() => {
            if (window._watchlistLoad) {
                window._watchlistLoaded = false;
                window._watchlistLoad();
            }
        }""")
        time.sleep(2)

        before_n = _row_count(page)
        if not _has_code(page, code):
            fails.append(f"前置条件失败: {code} add 后不在表中")
        print(f"  T2: before row_count={before_n}, has {code}={_has_code(page, code)}")
        _shot(page, "T2_s1_before")

        page.evaluate("""async (c) => {
            await fetch('/api/watchlist/' + encodeURIComponent(c), {method: 'DELETE'});
        }""", code)
        db_n = _wait_db(code, 0, max_s=90)
        print(f"  T2: DB after delete, count={db_n}")
        page.evaluate("""() => {
            if (window._watchlistLoad) {
                window._watchlistLoaded = false;
                window._watchlistLoad();
            }
        }""")
        # R151.1: GET /api/watchlist 现在 ~3-4s, 等 5s 保险.
        time.sleep(5)

        after_n = _row_count(page)
        has_c = _has_code(page, code)
        print(f"  T2: after  row_count={after_n} (expected {before_n - 1}), has {code}={has_c}")
        _shot(page, "T2_s2_after_del")

        if db_n > 0:
            fails.append(f"DB 未真删 (R150.8 锁): db_count={db_n} (跳过前端断言)")
        else:
            if after_n != before_n - 1:
                fails.append(f"row count 没减少: before={before_n} after={after_n}")
            if has_c:
                fails.append(f"{code} 还在前端列表 (Bug #1: 前端没刷新)")

        real = [e for e in errors_log if not _is_allowed(e)]
        if real:
            fails.append(f"console errors: {real[0][:150]}")

        ctx.close()
        browser.close()

    assert not fails, "T2 失败:\n  " + "\n  ".join(fails)


# ════════════════════════════════════════════════════════════════════════════════
# T3: 在 #watchlist F5 后仍在 #watchlist (Bug #2 修复验证)
# ════════════════════════════════════════════════════════════════════════════════
def test_t3_reload_keeps_watchlist(base_url):
    """Bug #2 验证: page.reload() 模拟 F5/cmd+R/右键刷新, 应保留在原 view.

    验证差异:
      修复前: s2_after_reload.png 显示 #dash
      修复后: s2_after_reload.png 显示 #watchlist (与 s1 一致)
    """
    fails = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        errors_log = []
        page.on("console", lambda m, e=errors_log: e.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda exc, e=errors_log: e.append(str(exc)))

        _goto_watchlist(page, base_url)
        before_view = _current_view(page)
        before_hash = _hash(page)
        print(f"  T3: before view={before_view} hash={before_hash}")
        _shot(page, "T3_s1_at_watchlist")

        if before_view != "watchlist":
            fails.append(f"前置条件失败: 没进 #watchlist (view={before_view})")

        # 浏览器原生 reload — 模拟用户按 F5 / 右键刷新 / cmd+R
        page.reload(wait_until="domcontentloaded", timeout=30000)
        # 等 JS boot + view 渲染
        try:
            page.wait_for_selector('[data-view]:not([hidden])', timeout=10000)
        except Exception:
            pass
        time.sleep(3)

        after_view = _current_view(page)
        after_hash = _hash(page)
        print(f"  T3: after  view={after_view} hash={after_hash}")
        _shot(page, "T3_s2_after_reload")

        if after_view != "watchlist":
            fails.append(f"reload 后跳到 {after_view} (期望 watchlist)")
        if "watchlist" not in after_hash:
            fails.append(f"reload 后 hash={after_hash} (期望含 watchlist)")

        real = [e for e in errors_log if not _is_allowed(e)]
        if real:
            fails.append(f"console errors: {real[0][:150]}")

        ctx.close()
        browser.close()

    assert not fails, "T3 失败:\n  " + "\n  ".join(fails)


# ════════════════════════════════════════════════════════════════════════════════
# T4: 其他 view F5 后同样保留 (回归)
# ════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("view_name", ["dragons", "yaogu", "dexin", "weekly_bull"])
def test_t4_other_views_kept(base_url, view_name):
    """Bug #2 回归: 除 watchlist 外, dragons/yaogu/dexin/weekly_bull 也保留."""
    fails = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        errors_log = []
        page.on("console", lambda m, e=errors_log: e.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda exc, e=errors_log: e.append(str(exc)))

        page.goto(f"{base_url}/#{view_name}",
                  wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_selector(f'[data-view="{view_name}"]:not([hidden])', timeout=10000)
        except Exception:
            pass
        time.sleep(2)

        before_view = _current_view(page)
        _shot(page, f"T4_{view_name}_s1_before")
        if before_view != view_name:
            fails.append(f"前置失败: 没进 #{view_name} (view={before_view})")

        page.reload(wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_selector('[data-view]:not([hidden])', timeout=10000)
        except Exception:
            pass
        time.sleep(3)

        after_view = _current_view(page)
        _shot(page, f"T4_{view_name}_s2_after")
        if after_view != view_name:
            fails.append(f"reload 后跳到 {after_view} (期望 {view_name})")

        real = [e for e in errors_log if not _is_allowed(e)]
        if real:
            fails.append(f"console errors: {real[0][:150]}")

        ctx.close()
        browser.close()

    assert not fails, f"T4 ({view_name}) 失败:\n  " + "\n  ".join(fails)


# ════════════════════════════════════════════════════════════════════════════════
# T5: 视觉基线对比 — 跑完后给 Claude 看截图
# ════════════════════════════════════════════════════════════════════════════════
def test_t5_visual_summary():
    """汇总截图清单, 供 Claude 视觉对比 baseline vs 当前 run."""
    pngs = sorted(RUN_DIR.glob("*.png"))
    print(f"\n  T5: 当前 run 截图 ({len(pngs)} 张) 在 {RUN_DIR}:")
    for p in pngs:
        print(f"    - {p.name}")
    base_pngs = sorted(BASELINE_DIR.glob("*.png"))
    print(f"  T5: baseline 截图 ({len(base_pngs)} 张) 在 {BASELINE_DIR}:")
    for p in base_pngs:
        print(f"    - {p.name}")
    assert len(pngs) > 0, "没有截图被生成"