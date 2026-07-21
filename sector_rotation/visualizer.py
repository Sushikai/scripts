#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
visualizer.py - matplotlib 板块轮动可视化
- 单日多板块资金对比（柱状图 + 折线图）
- 双日 T vs T-1 资金迁徙对比
- 主线信号标注（资金拐点 / 强弱断层）
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # 无头模式
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyArrowPatch
from matplotlib.font_manager import FontProperties
import numpy as np
import pandas as pd

# 中文字体（macOS 默认 PingFang）
plt.rcParams["font.sans-serif"] = ["PingFang SC", "Heiti SC", "Arial Unicode MS", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False


# ============================================================
# 颜色规则
# ============================================================
COLOR_MAIN = "#E63946"      # 主线红
COLOR_HOT = "#F4A261"       # 热门支线橙
COLOR_WEAK = "#8D99AE"      # 弱势灰
COLOR_BG = "#F1FAEE"        # 背景


# ============================================================
# 单日可视化
# ============================================================
def plot_single_day(report: dict, output_path: Path = None):
    """单日板块综合图表
    - 上：4 维评分雷达图 / 柱状图
    - 中：主力净流入柱状图
    - 下：板块指数相对强弱折线图（简化：相对涨幅对比）
    """
    sectors = report["sectors"]
    date_str = report["date"]
    if output_path is None:
        output_path = Path(__file__).parent / "charts" / f"single_{date_str}.png"
    output_path.parent.mkdir(exist_ok=True, parents=True)

    fig = plt.figure(figsize=(14, 10), facecolor=COLOR_BG)
    fig.suptitle(f"板块轮动复盘 · {date_str} · 沪深300 {report['hs300_change']:+.2f}%",
                 fontsize=16, fontweight="bold")

    # ===== 子图 1：综合评分柱状图 =====
    ax1 = plt.subplot2grid((3, 2), (0, 0), colspan=2)
    names = [s["板块"] for s in sectors]
    scores = [s["评分"] for s in sectors]
    colors = [COLOR_MAIN if s["是否主线"] else COLOR_HOT if s["评分"] >= 30 else COLOR_WEAK for s in sectors]
    bars = ax1.barh(names, scores, color=colors, edgecolor="black", linewidth=0.5)
    ax1.set_xlabel("综合评分 (0-100)", fontsize=11)
    ax1.set_title("板块综合评分排序", fontsize=13, fontweight="bold")
    ax1.invert_yaxis()
    for bar, s in zip(bars, sectors):
        mainline_mark = " ⭐" if s["是否主线"] else ""
        ax1.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
                 f"{s['评分']}{mainline_mark}", va="center", fontsize=10, fontweight="bold")
    ax1.grid(axis="x", alpha=0.3)
    ax1.set_xlim(0, max(scores) * 1.15 if scores else 100)

    # ===== 子图 2：主力净流入 =====
    ax2 = plt.subplot2grid((3, 2), (1, 0))
    inflow = [s["主力净流入_亿"] for s in sectors]
    colors_bar = [COLOR_MAIN if x > 0 else "#457B9D" for x in inflow]
    ax2.bar(names, inflow, color=colors_bar, edgecolor="black", linewidth=0.5)
    ax2.axhline(y=0, color="black", linewidth=0.5)
    ax2.axhline(y=5, color=COLOR_MAIN, linestyle="--", alpha=0.5, label="主线阈值 +5亿")
    ax2.set_title("主力资金净流入（亿）", fontsize=12, fontweight="bold")
    ax2.set_ylabel("亿元")
    ax2.tick_params(axis="x", rotation=20)
    ax2.legend(loc="upper right", fontsize=9)
    ax2.grid(axis="y", alpha=0.3)
    for i, (name, val) in enumerate(zip(names, inflow)):
        ax2.text(i, val + (3 if val >= 0 else -3), f"{val:+.1f}", ha="center",
                 fontsize=9, fontweight="bold")

    # ===== 子图 3：涨停数 + 最高连板 =====
    ax3 = plt.subplot2grid((3, 2), (1, 1))
    zt = [s["涨停数"] for s in sectors]
    lb = [s["最高连板"] for s in sectors]
    x = np.arange(len(names))
    width = 0.35
    ax3.bar(x - width / 2, zt, width, label="涨停数", color="#2A9D8F", edgecolor="black", linewidth=0.5)
    ax3.bar(x + width / 2, lb, width, label="最高连板", color="#E76F51", edgecolor="black", linewidth=0.5)
    ax3.set_xticks(x)
    ax3.set_xticklabels(names, rotation=20)
    ax3.set_title("涨停梯队", fontsize=12, fontweight="bold")
    ax3.legend()
    ax3.grid(axis="y", alpha=0.3)

    # ===== 子图 4：相对强弱 vs 板块涨跌 =====
    ax4 = plt.subplot2grid((3, 2), (2, 0), colspan=2)
    change = [s["板块涨跌幅"] for s in sectors]
    rel = [s["相对强弱"] for s in sectors]
    ax4.plot(names, change, marker="o", linewidth=2, label="板块涨跌", color="#264653")
    ax4.plot(names, rel, marker="s", linewidth=2, label="相对沪深300强弱", color="#E76F51")
    ax4.axhline(y=0, color="black", linewidth=0.5)
    ax4.set_title("板块涨跌幅 vs 相对强弱", fontsize=12, fontweight="bold")
    ax4.set_ylabel("%")
    ax4.tick_params(axis="x", rotation=20)
    ax4.legend()
    ax4.grid(alpha=0.3)
    for i, (c, r) in enumerate(zip(change, rel)):
        ax4.text(i, c + 0.2, f"{c:+.2f}", ha="center", fontsize=8)
        ax4.text(i, r - 0.5, f"{r:+.2f}", ha="center", fontsize=8, color="#E76F51")

    # 整体情绪提示
    mood_text = f"涨停总数 {report['zt_total']} | 连板 {report['zt_continuous']} | 晋级率 {report['upgrade_rate']}%"
    if report["high_sentiment"]:
        mood_text += " ⚠️ 情绪高潮"
    fig.text(0.5, 0.02, mood_text, ha="center", fontsize=10,
             bbox=dict(boxstyle="round", facecolor="white", edgecolor="black"))

    plt.tight_layout(rect=[0, 0.04, 1, 0.96])
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=COLOR_BG)
    plt.close()
    print(f"📊 单日图表已保存: {output_path}")
    return output_path


# ============================================================
# 双日对比
# ============================================================
def plot_compare(reports: list[dict], output_path: Path = None):
    """T vs T-1 双日资金对比
    reports: [T-1 report, T report]（按日期升序）
    """
    if len(reports) < 2:
        raise ValueError("需要至少 2 个日期的 report")
    prev, curr = reports[-2], reports[-1]
    if output_path is None:
        output_path = Path(__file__).parent / "charts" / f"compare_{curr['date']}_vs_{prev['date']}.png"
    output_path.parent.mkdir(exist_ok=True, parents=True)

    # 合并所有板块（取并集）
    all_sectors = []
    for r in reports:
        for s in r["sectors"]:
            if s["板块"] not in all_sectors:
                all_sectors.append(s["板块"])

    fig, axes = plt.subplots(2, 1, figsize=(12, 9), facecolor=COLOR_BG)
    fig.suptitle(f"板块资金迁徙对比 · {prev['date']} → {curr['date']}",
                 fontsize=15, fontweight="bold")

    # 上：T-1
    prev_data = {s["板块"]: s for s in prev["sectors"]}
    inflow_prev = [prev_data.get(s, {}).get("主力净流入_亿", 0) for s in all_sectors]
    axes[0].bar(all_sectors, inflow_prev,
                color=[COLOR_MAIN if v > 5 else COLOR_HOT if v > 0 else COLOR_WEAK for v in inflow_prev],
                edgecolor="black", linewidth=0.5)
    axes[0].axhline(y=5, color=COLOR_MAIN, linestyle="--", alpha=0.5, label="主线 +5亿")
    axes[0].axhline(y=0, color="black", linewidth=0.5)
    axes[0].set_title(f"{prev['date']}（T-1）", fontsize=12, fontweight="bold")
    axes[0].set_ylabel("主力净流入（亿）")
    axes[0].tick_params(axis="x", rotation=20)
    axes[0].grid(axis="y", alpha=0.3)
    axes[0].legend()
    for i, v in enumerate(inflow_prev):
        axes[0].text(i, v + (2 if v >= 0 else -2), f"{v:+.1f}", ha="center", fontsize=9)

    # 下：T
    curr_data = {s["板块"]: s for s in curr["sectors"]}
    inflow_curr = [curr_data.get(s, {}).get("主力净流入_亿", 0) for s in all_sectors]
    axes[1].bar(all_sectors, inflow_curr,
                color=[COLOR_MAIN if v > 5 else COLOR_HOT if v > 0 else COLOR_WEAK for v in inflow_curr],
                edgecolor="black", linewidth=0.5)
    axes[1].axhline(y=5, color=COLOR_MAIN, linestyle="--", alpha=0.5, label="主线 +5亿")
    axes[1].axhline(y=0, color="black", linewidth=0.5)
    axes[1].set_title(f"{curr['date']}（T）", fontsize=12, fontweight="bold")
    axes[1].set_ylabel("主力净流入（亿）")
    axes[1].tick_params(axis="x", rotation=20)
    axes[1].grid(axis="y", alpha=0.3)
    axes[1].legend()
    for i, v in enumerate(inflow_curr):
        axes[1].text(i, v + (2 if v >= 0 else -2), f"{v:+.1f}", ha="center", fontsize=9)

    # 迁徙标注：流出/流入变化最大的板块
    plt.tight_layout(rect=[0, 0.03, 1, 0.96])
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=COLOR_BG)
    plt.close()
    print(f"📊 双日对比图已保存: {output_path}")
    return output_path


# ============================================================
# 文字复盘
# ============================================================
def generate_text_report(report: dict, prev_report: dict = None) -> str:
    """生成文字复盘"""
    lines = []
    lines.append(f"# 板块轮动复盘 · {report['date']}")
    lines.append("")
    lines.append(f"**大盘环境**：沪深300 {report['hs300_change']:+.2f}%")
    lines.append(f"**情绪温度**：涨停 {report['zt_total']} 只，连板 {report['zt_continuous']} 只，晋级率 {report['upgrade_rate']}%"
                 f"{' ⚠️ **情绪高潮，谨慎新开仓**' if report['high_sentiment'] else ''}")
    lines.append("")
    lines.append("## 板块评分排序")
    for i, s in enumerate(report["sectors"], 1):
        mark = " ⭐ **主线**" if s["是否主线"] else ""
        lines.append(f"{i}. **{s['板块']}** - {s['评分']}{mark}")
        lines.append(f"   - 主力净流入 {s['主力净流入_亿']:+.1f}亿 | 板块涨跌 {s['板块涨跌幅']:+.2f}% | 涨停 {s['涨停数']} 只 | 最高连板 {s['最高连板']}")
        layer = s.get("个股分层", {})
        if layer.get("情绪龙头"):
            leaders = " / ".join([f"{x['名称']}({x['连板']}板)" for x in layer["情绪龙头"]])
            lines.append(f"   - 情绪龙头：{leaders}")
        if layer.get("趋势中军"):
            mids = " / ".join([f"{x['名称']}" for x in layer["趋势中军"][:3]])
            lines.append(f"   - 趋势中军：{mids}")

    lines.append("")
    lines.append("## 主线认定")
    if report["mainlines"]:
        lines.append(f"**当期主线**：{', '.join(report['mainlines'])}")
    else:
        lines.append("**当期主线**：无（资金/涨停/连板条件未全部满足）")

    if prev_report:
        lines.append("")
        lines.append("## 隔日轮动对比")
        prev_mainlines = set(prev_report["mainlines"])
        curr_mainlines = set(report["mainlines"])
        if prev_mainlines == curr_mainlines:
            lines.append(f"主线延续：{', '.join(curr_mainlines) or '无'}")
        else:
            new_main = curr_mainlines - prev_mainlines
            lost_main = prev_mainlines - curr_mainlines
            if lost_main:
                lines.append(f"⚠️ 主线**落幕**：{', '.join(lost_main)}")
            if new_main:
                lines.append(f"✅ 主线**新生**：{', '.join(new_main)}")

        # 资金迁徙
        lines.append("")
        lines.append("**资金迁徙（亿）**：")
        prev_data = {s["板块"]: s["主力净流入_亿"] for s in prev_report["sectors"]}
        for s in report["sectors"]:
            prev_v = prev_data.get(s["板块"], 0)
            curr_v = s["主力净流入_亿"]
            delta = curr_v - prev_v
            arrow = "↑" if delta > 0 else "↓"
            lines.append(f"- {s['板块']}：{prev_v:+.1f} → {curr_v:+.1f}（{arrow} {delta:+.1f}）")

    return "\n".join(lines)


# ============================================================
# CLI 测试
# ============================================================
if __name__ == "__main__":
    import json
    import sys
    date_str = sys.argv[1] if len(sys.argv) > 1 else "2026-07-11"
    report_file = Path(__file__).parent / "reports" / f"score_{date_str}.json"
    if not report_file.exists():
        print(f"找不到 {report_file}，请先跑 scorer.py")
        sys.exit(1)
    report = json.loads(report_file.read_text())

    chart_path = plot_single_day(report)
    print(f"\n{generate_text_report(report)}")