"""
tuixue_v3/telegram_push.py
推送选股结果 / 回测月报到 Telegram。
复用 lib_common.send_telegram（同步 REST 调用 + 4 次重试）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config as cfg
from . import blacklist as bl_mod
from . import lib_common as lc

log = logging.getLogger("tuixue_v3.telegram")


# ═══════════════════════════════════════════════════
# 工具：格式化
# ═══════════════════════════════════════════════════
def _fmt_pct(x: float | None) -> str:
    if x is None:
        return "-"
    return f"{x:+.2f}%"


def _fmt_yi(x: float | None) -> str:
    if x is None:
        return "-"
    return f"{x:.1f}亿"


def _fmt_section(title: str, body: str) -> str:
    return f"\n━━ {title} ━━\n{body}\n"


# ═══════════════════════════════════════════════════
# 选股结果推送
# ═══════════════════════════════════════════════════
def push_to_telegram(screen_result: dict, silent: bool = False) -> bool:
    """
    screen_result = run_stock_screen() 返回的字典
    推送：标题 + 每只候选明细 + 黑名单数 + 阻断原因
    """
    if not screen_result:
        return False

    date = screen_result.get("date", datetime.now().strftime("%Y%m%d"))
    picks = screen_result.get("candidates", [])
    stats_by_layer = screen_result.get("stats_by_layer", {})
    reason = screen_result.get("reason", "ok")
    elapsed = screen_result.get("elapsed_sec", 0)

    # 标题
    lines = [f"🎯 退学 v3 选股报告｜{date[:4]}-{date[4:6]}-{date[6:]}"]
    lines.append(f"用时 {elapsed}s · 健康 {screen_result.get('health', {}).get('spot_ok')}/{screen_result.get('health', {}).get('daily_ok')}/{screen_result.get('health', {}).get('sector_ok')}")
    lines.append(f"黑名单 {bl_mod.get_count()} 只 · 状态 {reason}")

    # 阻断原因
    if reason != "ok":
        for layer_name in ("l1", "l2", "l3", "l4"):
            lstats = stats_by_layer.get(layer_name, {})
            if lstats.get("block_reason") or lstats.get("cycle_blocked", 0) > 0:
                lines.append(f"⚠️ {layer_name} 阻断: {lstats.get('block_reason', lstats.get('cycle_detail', ''))}")
        if not picks:
            text = "\n".join(lines) + "\n\n今日无达标标的（宁缺毋滥）"
            return lc.send_telegram(text, parse_mode="text", silent=silent)

    # 每只候选
    lines.append(_fmt_section(
        f"候选池 {len(picks)} 只",
        "\n".join(_fmt_pick_line(i + 1, p) for i, p in enumerate(picks)) if picks else "（空）"
    ))

    # 每层通过统计
    lines.append(_fmt_section(
        "各层通过数",
        "\n".join(f"· {k}: {v.get('input', 0)} → {v.get('passed', 0)} 通过"
                  for k, v in stats_by_layer.items() if v)
    ))

    text = "\n".join(lines)
    return lc.send_telegram(text, parse_mode="text", silent=silent)


def _fmt_pick_line(idx: int, p: dict) -> str:
    code = p.get("code", "")
    name = p.get("name", "")
    sector = p.get("sector", "-")
    rr = p.get("rr_ratio", 0)
    turnover = p.get("turnover_yi", 0)
    g20 = p.get("gain_20d_pct", 0)
    tr = p.get("turnover_pct", 0)
    mv = p.get("free_mv_yi") or 0
    return (
        f"{idx}. {code} {name} ({sector})\n"
        f"   流通市值 {_fmt_yi(mv)} · 成交额 {_fmt_yi(turnover)} · 20d涨幅 {_fmt_pct(g20)} · 换手 {tr:.1f}% · RR {rr:.1f}"
    )


# ═══════════════════════════════════════════════════
# 回测月报推送
# ═══════════════════════════════════════════════════
def push_backtest_report(bt_result: dict, silent: bool = False) -> bool:
    """
    推送回测报告：综合 + 月度收益曲线 + 最佳参数（如有）
    """
    if not bt_result:
        return False

    cfg_section = bt_result.get("config", {})
    summary = bt_result.get("summary", {})
    monthly = bt_result.get("monthly", [])
    best_params = bt_result.get("best_params", {})  # 仅 optimizer 报告有

    lines = []
    title = "📊 退学 v3 回测月报" if not best_params else "🏆 退学 v3 优化报告"
    lines.append(title)
    lines.append(f"区间 {cfg_section.get('start', '')} → {cfg_section.get('end', '')}")
    lines.append(f"持仓 {cfg_section.get('hold_days', '?')} 日 · 每日 top {cfg_section.get('top_n', '?')} · 卖出 {cfg_section.get('sell_mode', 'rule')}")

    # 综合
    lines.append(_fmt_section("综合", "\n".join([
        f"· 总交易: {summary.get('trades', 0)} 笔（胜 {summary.get('wins', 0)} / 负 {summary.get('losses', 0)}）",
        f"· 胜率: {summary.get('win_rate_pct', 0)}%",
        f"· 平均收益: {_fmt_pct(summary.get('avg_return_pct', 0))}",
        f"· 平均盈利: {_fmt_pct(summary.get('avg_win_pct', 0))} / 平均亏损: {_fmt_pct(summary.get('avg_loss_pct', 0))}",
        f"· 盈亏比: {summary.get('profit_factor', 0)}",
        f"· 月均收益: {_fmt_pct(summary.get('monthly_avg_return_pct', 0))}",
        f"· 最大回撤: {summary.get('max_drawdown_pct', 0)}%",
        f"· 最佳单笔: {_fmt_pct(summary.get('best_trade_pct', 0))} / 最差: {_fmt_pct(summary.get('worst_trade_pct', 0))}",
    ])))

    # 月度
    if monthly:
        lines.append(_fmt_section(
            "月度收益",
            "\n".join(
                f"· {m['month']}: {_fmt_pct(m.get('sum_return_pct', 0))}（{m.get('trades', 0)} 笔 / 胜率 {m.get('win_rate_pct', 0)}%）"
                for m in monthly
            )
        ))

    # 最佳参数（optimizer 报告）
    if best_params:
        lines.append(_fmt_section(
            "最佳参数",
            "\n".join(f"· {k}: {v}" for k, v in best_params.items())
        ))
        lines.append(f"\n得分: {bt_result.get('best_score', 0)}")

    lines.append(f"\n报告 JSON: ~/scripts/stock/tuixue_v3/reports/")

    text = "\n".join(lines)
    return lc.send_telegram(text, parse_mode="text", silent=silent)


# ═══════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════
def _cli():
    import argparse
    p = argparse.ArgumentParser(description="退学 v3 推送")
    p.add_argument("--type", choices=["screen", "backtest"], required=True)
    p.add_argument("--file", help="结果 JSON 文件路径")
    p.add_argument("--silent", action="store_true")
    args = p.parse_args()

    if args.file:
        data = json.loads(Path(args.file).read_text())
    else:
        log.error("请通过 --file 指定 JSON 文件")
        return

    if args.type == "screen":
        push_to_telegram(data, silent=args.silent)
    else:
        push_backtest_report(data, silent=args.silent)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _cli()