"""
tests/test_visual_tokens.py — 视觉规则化扫描 + AA 对比度体检

跑法:
    PYTHONPATH=. python3 -m pytest tests/test_visual_tokens.py -v
    PYTHONPATH=. python3 -m pytest tests/test_visual_tokens.py::TestAAContrast -v

Purpose:
  · 防回归 — 新增硬编码 #xxx / Npx 立刻 fail
  · 不动业务 — 只扫样式相关行
  · AA 合规 — 计算 token 对照表真实对比度
"""
from __future__ import annotations
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB_STATIC = ROOT / "web" / "static"
TOKENS = WEB_STATIC / "tokens.css"

# ────────────────────────────── 视觉巡检规则化 ──────────────────────────────

# JS / HTML 里允许的 token ("白名单"); 用于着色: 业务代码出现的非白名单 hex → fail
# 含义: 这些字符串是 token 名/语义色,不算硬编码
SEMANTIC_COLOR_TOKENS = {
    "var(--up)", "var(--down)", "var(--up-soft)", "var(--down-soft)",
    "var(--ink)", "var(--ink-1)", "var(--ink-2)", "var(--ink-3)", "var(--ink-4)",
    "var(--bg)", "var(--bg-1)", "var(--bg-2)", "var(--bg-3)", "var(--bg-page)",
    "var(--bg-card)", "var(--bg-hover)", "var(--bg-card-2)",
    "var(--accent)", "var(--accent-2)", "var(--accent-3)",
    "var(--accent-grad)", "var(--accent-grad-soft)",
    "var(--cat-northbound)", "var(--cat-institution)", "var(--cat-retail_lhasa)",
    "var(--cat-quant)", "var(--cat-hot_tier1)", "var(--cat-hot_tier2)",
    "var(--cat-hot_tier3)", "var(--cat-unknown)",
    "var(--warn)", "var(--star-gold)", "var(--star-gold-soft)",
    "var(--domain-orange)", "var(--domain-orange-soft)",
}

# 字号 token 闭合白名单 (任何不符合的数字 px 直接 fail)
TEXT_TOKEN_VALUES = {9, 9.5, 10, 10.5, 11, 11.5, 12, 12.5, 13, 13.5, 14, 14.5, 15, 16, 17, 18,
                     20, 22, 23, 24, 26, 28, 32, 36, 40, 42, 48, 56, 64}
# 但业务代码里仍有 13/14/14.5/16 这些口径直接使用是允许的(局部强约束)
TEXT_TOKEN_FUNCS = {"var(--text-", "calc(", "clamp(", "min(", "max(", "inherit", "0", "currentColor"}

# 圆角 token 白名单 (含 999 / 9999 = --radius-full 等价)
RADIUS_TOKEN_VALUES = {2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, 18, 20, 22, 28, 999, 9999}
RADIUS_TOKEN_FUNCS = {"var(--radius-", "calc(", "inherit", "50%", "9999px", "0", "100%"}

# 扫描目标文件
SCAN_FILES = [
    WEB_STATIC / "index.html",
    WEB_STATIC / "style.css",
    *sorted(WEB_STATIC.glob("view-*.js")),
    WEB_STATIC / "app.js",
]


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")


# ─────────────────────────── 测试 1: 硬编码 hex 颜色 ───────────────────────

HEX_RE = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")


class TestNoHardcodedColors:
    """view-*.js / index.html / app.js 业务代码不得出现硬编码 #hex 颜色"""

    @pytest.mark.visual
    @pytest.mark.parametrize("path", [p for p in SCAN_FILES if p.suffix in {".js", ".html"}],
                             ids=lambda p: p.name)
    def test_no_hex_color_in_business(self, path: Path):
        text = _read(path)
        # 排除注释行与 console 文案
        offenders = []
        for ln, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("//", "/*", "*", "#", '"#', "'#")) or "console." in stripped:
                continue
            # 排除 markdown / url 文本 / data: URI / 单元测试自身
            if "data:" in line or "://" in line or "<a" in line.lower():
                continue
            for m in HEX_RE.finditer(line):
                hexv = m.group(0).lower()
                # 限定 #XXX / #XXXXXX; #FFFFFF 也算
                if len(m.group(1)) in (3, 6):
                    offenders.append((ln, hexv, line.strip()[:120]))
        if offenders:
            msg = "\n".join(f"  L{ln}: {hexv}  in  {ctx}" for ln, hexv, ctx in offenders[:30])
            pytest.fail(f"{path.name}: 发现 {len(offenders)} 处硬编码 hex 颜色 (≤30 行):\n{msg}")

    @pytest.mark.visual
    def test_app_js_no_hex_outside_known_palettes(self):
        """app.js 允许有几处对照表色,但必须集中在 CHART_PALETTE / COLORS 等命名常量"""
        text = _read(WEB_STATIC / "app.js")
        # 找不是被引号包围的 #XXX (说明不是字符串字面量)
        bad = []
        for ln, line in enumerate(text.splitlines(), 1):
            if "'#" in line or '"#' in line or ":`#" in line:
                continue
            if line.strip().startswith(("/", "*", "#")):
                continue
            for m in HEX_RE.finditer(line):
                # 仅 fail 行内同时有 'chart'/'color'/'fill'/'stroke' 等关键词,避免误报
                low = line.lower()
                if any(k in low for k in ("chart", "color", "fill", "stroke", "echart")):
                    bad.append((ln, m.group(0), line.strip()[:100]))
        if bad:
            msg = "\n".join(f"  L{ln}: {hexv}  in  {ctx}" for ln, hexv, ctx in bad[:20])
            pytest.fail(f"app.js: {len(bad)} 处疑似硬编码图表色 (≤20 行):\n{msg}")


# ─────────────────────────── 测试 2: 硬编码字号 ───────────────────────────

FS_RE = re.compile(r"font-size\s*:\s*([0-9.]+)px")


class TestNoHardcodedFontSize:
    @pytest.mark.visual
    @pytest.mark.parametrize("path", [WEB_STATIC / "index.html",
                                     WEB_STATIC / "style.css",
                                     *sorted(WEB_STATIC.glob("view-*.js"))],
                             ids=lambda p: p.name)
    def test_font_size_uses_known_tokens(self, path: Path):
        text = _read(path)
        bad = []
        for ln, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("//", "/*", "*")):
                continue
            for m in FS_RE.finditer(line):
                val = float(m.group(1))
                # 白名单: var(--text-*); 数字直接出现 → 必须命中白名单
                if "var(--text-" in line or "var(--text-" in stripped:
                    continue
                # 在 CSS 注释里允许
                if "*" in stripped[:20]:
                    continue
                if val not in TEXT_TOKEN_VALUES:
                    bad.append((ln, val, line.strip()[:100]))
        if bad:
            msg = "\n".join(f"  L{ln}: font-size:{v}px  in  {ctx}" for ln, v, ctx in bad[:30])
            pytest.fail(f"{path.name}: {len(bad)} 处非 token 字号 (≤30 行):\n{msg}")


# ─────────────────────────── 测试 3: 硬编码圆角 ───────────────────────────

BR_RE = re.compile(r"border-radius\s*:\s*([0-9.]+)px")


class TestNoHardcodedBorderRadius:
    @pytest.mark.visual
    @pytest.mark.parametrize("path", [WEB_STATIC / "index.html",
                                     WEB_STATIC / "style.css",
                                     *sorted(WEB_STATIC.glob("view-*.js"))],
                             ids=lambda p: p.name)
    def test_border_radius_uses_known_tokens(self, path: Path):
        text = _read(path)
        bad = []
        for ln, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("//", "/*", "*")):
                continue
            for m in BR_RE.finditer(line):
                val = float(m.group(1))
                if "var(--radius-" in line:
                    continue
                if val not in RADIUS_TOKEN_VALUES:
                    bad.append((ln, val, line.strip()[:100]))
        if bad:
            msg = "\n".join(f"  L{ln}: border-radius:{v}px  in  {ctx}" for ln, v, ctx in bad[:30])
            pytest.fail(f"{path.name}: {len(bad)} 处非 token 圆角 (≤30 行):\n{msg}")


# ─────────────────────────── 测试 4: AA 对比度 (token 配对) ───────────────

def _hex_to_rgb(s: str):
    s = s.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) == 4:
        s = "".join(c * 2 for c in s)
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def _rel_lum(rgb):
    def c(v):
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * c(r) + 0.7152 * c(g) + 0.0722 * c(b)


def _contrast(c1, c2):
    l1, l2 = _rel_lum(c1), _rel_lum(c2)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


# 从 tokens.css 抽 --xxx: value 颜色
TOK_LINE_RE = re.compile(r"^\s*(--[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8}|rgba?\([^)]+\)|hsla?\([^)]+\))\s*;", re.M)


def _parse_token_colors() -> dict[str, str]:
    """返回 {主题名: {token 名: rgba}} 字典
       扫描 tokens.css + style.css 主题块 (refactor 后只需 tokens.css)"""
    out = {"light": {}, "dark": {}}
    cur = "light"
    files_to_scan = [TOKENS, WEB_STATIC / "style.css"]
    for fp in files_to_scan:
        if not fp.exists():
            continue
        text = _read(fp)
        for line in text.splitlines():
            if "[data-theme=\"dark\"]" in line:
                cur = "dark"
                continue
            if "[data-theme=\"light\"]" in line:
                cur = "light"
                continue
            for m in TOK_LINE_RE.finditer(line):
                name, val = m.group(1), m.group(2)
                if val.startswith("#"):
                    out[cur][name] = val
                else:
                    out[cur][name] = val
    return out


def _alpha_compose(fg: tuple, bg: tuple, alpha: float):
    return tuple(int(round(fg[i] * alpha + bg[i] * (1 - alpha))) for i in range(3))


def _to_rgb_with_bg(value: str, page_bg: tuple):
    """将 #xxxxxx 或 rgba(...) 相对于 page_bg 合成 → 返回纯 RGB"""
    if value.startswith("#"):
        return _hex_to_rgb(value)
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,?\s*([\d.]+)?\s*\)", value)
    if m:
        r, g, b, a = int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4) or 1)
        if a >= 0.999:
            return (r, g, b)
        return _alpha_compose((r, g, b), page_bg, a)
    return None


class TestAAContrast:
    """每主题下,8 个文字 token 对 4 个背景 token 必须满足 AA (>= 4.5:1)
       注: --ink-3 / --ink-4 是辅助级,允许 3.0:1 (WCAG 大字/LV2)
    """

    @pytest.fixture(scope="class")
    def token_table(self):
        return _parse_token_colors()

    # 不查 透明 / 玻璃态 / accent 等边角色; 只查文字 vs 背景
    INK_TOKENS = ["--ink-1", "--ink-2", "--ink-3", "--ink-4"]
    BG_TOKENS = ["--bg-page", "--bg-1", "--bg-2", "--bg-3", "--bg-card"]
    # 每个 token 的最小对比度阈值
    INK_THRESHOLD = {
        "--ink-1": 7.0,
        "--ink-2": 4.5,
        "--ink-3": 3.0,    # 仅用于 >=18px 或 bold 14px
        "--ink-4": 3.0,
    }

    @pytest.mark.visual
    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_all_ink_tokens_aa_over_bg(self, theme: str, token_table):
        tab = token_table[theme]
        failures = []
        for bg_name in self.BG_TOKENS:
            bg_val = tab.get(bg_name)
            if not bg_val:
                continue
            # 用 page bg 合成
            bg_raw = _to_rgb_with_bg(bg_val, (255, 255, 255) if theme == "light" else (11, 14, 20))
            if bg_raw is None:
                continue
            for ink_name in self.INK_TOKENS:
                ink_val = tab.get(ink_name)
                if not ink_val:
                    continue
                ink_raw = _to_rgb_with_bg(ink_val, bg_raw)
                if ink_raw is None:
                    continue
                ratio = _contrast(ink_raw, bg_raw)
                thr = self.INK_THRESHOLD[ink_name]
                if ratio < thr:
                    failures.append((bg_name, ink_name, round(ratio, 2), thr))
        if failures:
            msg = "\n".join(f"  {b} vs {i}: {r} (阈值 {t})"
                            for b, i, r, t in failures)
            pytest.fail(f"{theme} 主题 {len(failures)} 处 AA 不合规:\n{msg}")

    @pytest.mark.visual
    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_up_down_against_bg(self, theme: str, token_table):
        """CN 涨跌色 vs 卡片背景 — 必须 ≥ 3:1 (色觉无障碍)
           注意: --up-soft / --down-soft 是 tinted 背景,不做对比度检测"""
        tab = token_table[theme]
        bg = _to_rgb_with_bg(tab.get("--bg-card") or tab["--bg-1"], (255, 255, 255) if theme == "light" else (11, 14, 20))
        for name in ("--up", "--down", "--up-strong", "--down-strong"):
            val = tab.get(name)
            if not val:
                continue
            rgb = _to_rgb_with_bg(val, bg)
            if rgb is None:
                continue
            r = _contrast(rgb, bg)
            if r < 3.0:
                pytest.fail(f"{theme} {name}: 与 --bg 对比度 {r:.2f} < 3.0")


# ─────────────────────────── 测试 5: token 配对完整 ───────────────────────

class TestTokenPairCoverage:
    """[data-theme=dark] 必须覆盖 :root 中所有颜色 token,否则亮/暗不对称"""

    @pytest.mark.visual
    def test_dark_theme_completeness(self):
        """聚合 tokens.css + style.css 中所有 :root 颜色 token,验证 [data-theme="dark"] 块都已覆盖"""
        root_vars = set()
        theme_dark_vars = set()
        in_dark = False

        files_to_scan = [TOKENS, WEB_STATIC / "style.css"]
        for fp in files_to_scan:
            if not fp.exists():
                continue
            text = _read(fp)
            in_root = True
            for line in text.splitlines():
                if "[data-theme=\"dark\"]" in line:
                    in_root = False
                    in_dark = True
                    continue
                if "[data-theme=\"light\"]" in line:
                    in_root = False
                    in_dark = False
                    continue
                for m in TOK_LINE_RE.finditer(line):
                    name = m.group(1)
                    if in_root and (any(name.startswith(p) for p in ("--ink", "--bg", "--up", "--down"))):
                        root_vars.add(name)
                    if in_dark:
                        theme_dark_vars.add(name)
        missing = sorted(root_vars - theme_dark_vars)
        if missing:
            pytest.fail(f"dark 主题缺 {len(missing)} 个 token 覆盖:\n  "
                        + "\n  ".join(missing))
