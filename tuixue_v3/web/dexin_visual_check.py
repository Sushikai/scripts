"""web/dexin_visual_check.py — 视觉验证模块 (matplotlib + MiniMax M3 vision)

设计: 给定 dexin Top N 候选, 对每只:
  1. 拉日线 OHLCV
  2. 用 DexinTrendAgent 重算阶段 (返回 phase + cz_high + kill_idx + gain_idx)
  3. matplotlib 画 K线图 + 阶段 chip + 锚点支撑虚线 + 关键点位
  4. 输出 PNG 到 web/static/dexin_visual/{code}.png
  5. 调 MiniMax vision, prompt 询问此 K线图是否对应 "藏诈→虚杀→得鑫" 完整链条
  6. 解析末尾 PASS/FAIL, 返回结果列表

模块依赖是可选的: matplotlib + vision 缺失时由 dexin_screener.visual_verify 直接降级,
此模块本身不会导入失败 (try/except 在 import 时就生效).
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

log = logging.getLogger("tuixue_v3.dexin_visual")

# 用 __file__ 锚到 web/static/dexin_visual, 不依赖 cwd (server 在 tuixue_v3/ 跑但 cwd 偶尔是 scripts/)
_HERE = Path(__file__).resolve().parent  # web/
CHART_DIR = _HERE / "static" / "dexin_visual"
CHART_DIR.mkdir(parents=True, exist_ok=True)


async def run_visual_verify(picks: list[dict]) -> tuple[list[dict], list[str]]:
    """对 Top N 候选生成 K线图 + 调用 MiniMax M3 vision 评估.

    返回 (results, chart_paths):
      results = [{code, name, vision_verdict, vision_excerpt, chart_path}, ...]
      chart_paths = [相对路径列表]

    不抛异常: 任何一只失败降级为 ERROR verdict, 不影响其他候选.
    """
    results: list[dict] = []
    chart_paths: list[str] = []
    for stk in picks:
        code = stk.get("code")
        name = stk.get("name") or code
        try:
            chart_path, verdict, excerpt = await _verify_one(code, name)
            results.append({
                "code": code,
                "name": name,
                "vision_verdict": verdict,
                "vision_excerpt": excerpt,
                "chart_path": chart_path,
            })
            if chart_path:
                chart_paths.append(chart_path)
        except Exception as e:
            log.warning("verify code=%s failed: %s", code, e)
            results.append({
                "code": code,
                "name": name,
                "vision_verdict": "ERROR",
                "vision_excerpt": str(e)[:200],
                "chart_path": None,
            })
    return results, chart_paths


async def _verify_one(code: str, name: str) -> tuple[str | None, str, str]:
    """单股视觉验证."""
    # 1. 拉日线
    from .. import data_layer
    from .dexin_screener import DexinTrendAgent
    df = await asyncio.to_thread(_fetch_daily, code)
    if df is None or len(df) < 30:
        return None, "SKIP", f"日线不足 (n={len(df) if df is not None else 0}), 跳过视觉验证"

    # 2. 重算阶段
    agent = DexinTrendAgent()
    detect = agent.detect(df)
    phase = detect.get("phase") or "none"
    phase_dates = detect.get("phase_dates") or {}
    signals = detect.get("signals") or {}

    # 3. 画图
    chart_path = await asyncio.to_thread(
        _render_chart, code, df, phase, phase_dates, signals
    )

    # 4. 调 vision
    verdict, excerpt = await _call_vision(code, name, phase, chart_path)
    rel_path = f"/static/dexin_visual/{code}.png"
    return rel_path, verdict, excerpt


def _fetch_daily(code: str):
    """拉日线 OHLCV (>=30 根). 同步 helper 走 to_thread."""
    try:
        from .. import data_layer  # tuixue_v3 root package
    except Exception:
        try:
            import data_layer  # 兼容脚本模式
        except Exception:
            log.warning("data_layer 模块不可达")
            return None
    try:
        # 优先 fetch_daily(单股), 兼容 daily(别名)
        df = None
        if hasattr(data_layer, "fetch_daily"):
            df = data_layer.fetch_daily(code, 60)
        elif hasattr(data_layer, "daily"):
            df = data_layer.daily(code)
        if df is None or len(df) == 0:
            return None
        if len(df) < 30:
            return None
        return df.tail(60).reset_index(drop=True)
    except Exception as e:
        log.warning("data_layer fetch_daily(%s) failed: %s", code, e)
        return None


def _render_chart(code: str, df, phase: str, phase_dates: dict, signals: dict) -> str | None:
    """matplotlib 画 K线 + 阶段 chip + 锚点支撑 + 关键点位 → PNG."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        # 配 CJK 字体, 否则中文阶段 chip 全部 □
        try:
            from matplotlib import font_manager as _fm
            for _fp in [
                "/System/Library/Fonts/PingFang.ttc",
                "/System/Library/Fonts/STHeiti Medium.ttc",
                "/System/Library/Fonts/STHeiti Light.ttc",
            ]:
                try:
                    _fm.fontManager.addfont(_fp)
                except Exception:
                    pass
            plt.rcParams["font.sans-serif"] = ["PingFang SC", "STHeiti", "Heiti TC", "Arial Unicode MS", "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
        except Exception:
            pass
    except Exception:
        return None

    try:
        dates = list(df["日期"])[-60:]
        opens = [float(x) for x in df["开盘"].tail(60)]
        closes = [float(x) for x in df["收盘"].tail(60)]
        highs = [float(x) for x in df["最高"].tail(60)]
        lows = [float(x) for x in df["最低"].tail(60)]
    except Exception as e:
        log.warning("render chart data prep failed: %s", e)
        return None

    fig, ax = plt.subplots(figsize=(11, 5.5), dpi=120)
    x = list(range(len(dates)))
    for i in range(len(dates)):
        color = "#c1464a" if closes[i] >= opens[i] else "#2ea067"
        body_lo = min(opens[i], closes[i])
        body_hi = max(opens[i], closes[i])
        ax.vlines(x[i], lows[i], highs[i], color=color, linewidth=0.8)
        ax.vlines(x[i], body_lo, body_hi, color=color, linewidth=3)

    ax.set_xticks(x[::max(1, len(x) // 6)])
    ax.set_xticklabels([str(d)[:10] for d in dates[::max(1, len(x) // 6)]],
                       rotation=30, fontsize=8)
    ax.set_title(f"{code} {phase} · 量变术视觉验证", fontsize=12, fontweight="bold")
    ax.grid(True, alpha=0.25)

    # 锚点支撑虚线
    anchor = (signals or {}).get("anchor_price")
    if anchor:
        ax.axhline(float(anchor), color="#888", linestyle="--", linewidth=0.8, alpha=0.7)
        ax.text(len(x) - 0.5, float(anchor), f" 锚点 {anchor:.2f}",
                fontsize=8, color="#555", va="center")

    # 阶段 chip 注释 (在最高 high 上方)
    cz_end_label = phase_dates.get("藏诈日")
    kill_label = phase_dates.get("虚杀日")
    gain_label = phase_dates.get("得鑫日")
    cluster = max(highs) * 1.02
    if cz_end_label:
        try:
            cz_idx = next((i for i, d in enumerate(dates) if str(d)[:10].endswith(str(cz_end_label)[5:])),
                          None)
            if cz_idx is not None:
                ax.annotate(f"藏诈 {cz_end_label}", (cz_idx, highs[cz_idx]),
                            xytext=(0, 12), textcoords="offset points",
                            fontsize=9, color="#c14a3a",
                            arrowprops=dict(arrowstyle="-", color="#c14a3a", lw=0.8))
        except Exception:
            pass
    if kill_label:
        try:
            k_idx = next((i for i, d in enumerate(dates) if str(d)[:10].endswith(str(kill_label)[5:])),
                         None)
            if k_idx is not None:
                ax.annotate(f"虚杀 {kill_label}", (k_idx, lows[k_idx]),
                            xytext=(0, -16), textcoords="offset points",
                            fontsize=9, color="#5b8def",
                            arrowprops=dict(arrowstyle="-", color="#5b8def", lw=0.8))
        except Exception:
            pass
    if gain_label:
        try:
            g_idx = next((i for i, d in enumerate(dates) if str(d)[:10].endswith(str(gain_label)[5:])),
                         None)
            if g_idx is not None:
                ax.annotate(f"得鑫 {gain_label}", (g_idx, highs[g_idx]),
                            xytext=(0, 14), textcoords="offset points",
                            fontsize=10, color="#2a8a4a", fontweight="bold",
                            arrowprops=dict(arrowstyle="-", color="#2a8a4a", lw=1.0))
        except Exception:
            pass

    out_path = CHART_DIR / f"{code}.png"
    try:
        fig.tight_layout()
        fig.savefig(out_path, dpi=110, bbox_inches="tight")
        plt.close(fig)
    except Exception as e:
        log.warning("savefig failed: %s", e)
        plt.close(fig)
        return None
    return str(out_path)


async def _call_vision(code: str, name: str, phase: str, chart_path: str | None) -> tuple[str, str]:
    """调 MiniMax M3 vision 判定 K线图是否符合量变术形态.

    返回 (verdict, excerpt). verdict ∈ {PASS, FAIL, SKIP, ERROR, UNAVAILABLE}.
    """
    if not chart_path or not Path(chart_path).exists():
        return "SKIP", "未生成阶段图, 跳过视觉验证"

    prompt = (
        f"这张是 {code} {name} 的日 K线图, 阶段判定为「{phase}」。"
        "请判断: 1) 是否呈现 藏诈(温和上涨+异动) → 虚杀(回撤不破支撑) → 得鑫(放量突破) 的完整链条? "
        "2) 锚点支撑(虚线附近)是否守得住? "
        "3) 整体走势是否符合'主力建仓→洗盘→主升'? "
        "用中文 4 句话内回答, 最后一行只写 PASS 或 FAIL。"
    )

    try:
        from . import ai_client
    except Exception:
        return "UNAVAILABLE", "ai_client 不可达"

    api_key = os.environ.get("MINIMAX_API_KEY") or os.environ.get("MINIMAX_KEY")
    if not api_key:
        return "UNAVAILABLE", "MINIMAX_API_KEY 未配置, 跳过视觉验证"

    try:
        data_url = await asyncio.to_thread(_png_to_data_url, chart_path)
        # 仅 base64 部分提取
        b64 = data_url.split(",", 1)[1] if "," in data_url else data_url
        body = {
            "model": ai_client.default_model(),
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt[:1500]},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }],
            "temperature": 0.1,
        }
        spec = ai_client.CallSpec(
            url=ai_client.default_url(),
            headers=ai_client.headers(api_key),
            body=body,
            name="dexin_visual_verify",
            model=ai_client.default_model(),
            timeout=80.0,
            attempts=(1, 2),
            # M3 vision + reasoning_content 会消耗大量 token, 复用截图解析的配比
            max_tokens_alts=(8000, 12000),
        )
        text, _parsed, _meta = await asyncio.to_thread(ai_client.call, spec)
        text = (text or "").strip()
        verdict = "PASS" if "PASS" in text[-20:].upper() else (
            "FAIL" if "FAIL" in text[-20:].upper() else "UNKNOWN")
        excerpt = text[:200]
        return verdict, excerpt
    except Exception as e:
        return "ERROR", f"vision 调用失败: {str(e)[:200]}"


def _png_to_data_url(path: str) -> str:
    """PNG 文件转 data:image/png;base64,xxx dataURL."""
    import base64
    p = Path(path)
    data = p.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{b64}"
