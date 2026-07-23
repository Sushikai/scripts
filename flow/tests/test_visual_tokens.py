"""visual token 扫描:禁止 view-*.js / base.css / tokens.css 之外出现硬编码色。

这是 tuixue_v3 的同款铁律:任何颜色/字号/圆角都必须从 tokens.css 取。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

# 允许颜色格式
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_RGB_COLOR = re.compile(r"rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+")
_HSL_COLOR = re.compile(r"hsla?\(\s*\d+")

# 允许出现硬编码颜色的文件(品牌字面量源)
_WHITELIST_FILES = {"tokens.css", "sw.js"}


def _scan_js_colors() -> list[tuple[str, int, str]]:
    """扫所有 js 文件,返回 (file, line, line_text) 命中。"""
    out = []
    js_dir = FRONTEND / "js"
    for p in js_dir.glob("*.js"):
        rel = p.relative_to(ROOT).as_posix()
        if "vendor" in rel:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if _HEX_COLOR.search(line) or _RGB_COLOR.search(line) or _HSL_COLOR.search(line):
                out.append((rel, i, line.strip()[:80]))
    return out


def _scan_css_files_outside_tokens() -> list[tuple[str, int, str]]:
    """扫 base.css(除 tokens.css 本身)的硬编码颜色。"""
    out = []
    base = FRONTEND / "css" / "base.css"
    if base.exists():
        for i, line in enumerate(base.read_text(encoding="utf-8").splitlines(), 1):
            # base.css 允许 rgba(0,0,0,.35) 这种阴影微调
            if _HEX_COLOR.search(line):
                out.append(("frontend/css/base.css", i, line.strip()[:80]))
    return out


def test_no_hex_colors_in_js():
    """view-*.js 不允许写死 #xxxx 颜色,必须用 var(--xxx)。"""
    hits = _scan_js_colors()
    bad = [h for h in hits if _HEX_COLOR.search(h[2])]
    assert not bad, f"硬编码 hex 色出现在 JS:\n" + "\n".join(f"{f}:{i}: {t}" for f, i, t in bad)


def test_no_rgb_in_js():
    """view-*.js 不允许 rgb()/rgba()。"""
    hits = _scan_js_colors()
    bad = [h for h in hits if _RGB_COLOR.search(h[2])]
    assert not bad, f"硬编码 rgb/rgba 出现在 JS:\n" + "\n".join(f"{f}:{i}: {t}" for f, i, t in bad)


def test_no_hex_colors_in_base_css():
    """base.css 不写死 hex 颜色(阴影微调 rgba 允许)。"""
    bad = _scan_css_files_outside_tokens()
    assert not bad, f"硬编码 hex 色出现在 base.css:\n" + "\n".join(f"{f}:{i}: {t}" for f, i, t in bad)


def test_tokens_css_exists():
    assert (FRONTEND / "css" / "tokens.css").exists()


def test_tokens_brand_color_present():
    """粉紫品牌 --brand-1 必须在 tokens.css 里。"""
    content = (FRONTEND / "css" / "tokens.css").read_text(encoding="utf-8")
    assert "--brand-1" in content
    assert "ff6ad5" in content.lower()  # 粉
    assert "c779ff" in content.lower()  # 紫


def test_index_html_references_static():
    """index.html 引用 /static/css/tokens.css /static/js/core.js。"""
    idx = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert "/static/css/tokens.css" in idx
    assert "/static/js/core.js" in idx
    assert "/static/js/app.js" in idx


def test_font_family_in_tokens():
    content = (FRONTEND / "css" / "tokens.css").read_text(encoding="utf-8")
    assert "--font-sans" in content
    assert "PingFang" in content or "system" in content.lower()


def test_safe_area_in_base_or_index():
    """safe-area inset 必须出现在 base.css 或 index.html。"""
    base = (FRONTEND / "css" / "base.css").read_text(encoding="utf-8")
    idx = (FRONTEND / "index.html").read_text(encoding="utf-8")
    combined = base + "\n" + idx
    assert "safe-area-inset" in combined