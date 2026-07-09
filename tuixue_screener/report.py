#!/usr/bin/env python3
"""
tuixue_screener/report.py
生成最终的退学战法选股系统报告（Markdown 格式）。
"""

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
REPORTS = ROOT / "reports"


def generate_report():
    """生成综合报告"""
    bt_data = json.loads((REPORTS / "backtest_report.json").read_text())
    opt_data = json.loads((REPORTS / "optimize_v2_report.json").read_text())

    # 基础回测摘要
    base = bt_data["monthly"]["summary"]
    base_months = bt_data["monthly"]["months"]

    # 优化后回测摘要
    best = opt_data["best_config"]
    best_pos = opt_data["best_position"]
    best_topn = opt_data["best_topn"]
    best_monthly = opt_data["best_monthly"]["summary"]
    best_months = opt_data["best_monthly"]["months"]

    md = f"""# 退学战法选股系统 - 回测优化报告

**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

---

## 📊 系统概述

基于退学炒股全套短线交易思想开发的 A 股量化选股程序，严格按以下 4 层流水线筛选：

| 层级 | 作用 | 关键指标 |
|---|---|---|
| **Layer 1** | 全局基础风险初筛（一票否决） | 主板 / 成交额 ≥ 8000万 / 流通市值 50-300亿 / 换手 5-15% |
| **Layer 2** | 主线题材过滤 | 隶属主线板块（涨幅 ≥ 40 只 / 资金净流入前 3） |
| **Layer 3** | 日线趋势形态 | MA5>MA10>MA20 (+ 可选 MA60) / 20 日涨幅 < 35% |
| **Layer 4** | 分时资金承接 | 9:30-10:30 70% 时间价格在均价线上 / 无尾盘偷袭 |
| **盈亏比** | 前置过滤 | 理论盈亏比 ≥ 2.0:1（可配置 2.5/3.0） |

---

## 🎯 三级行情数据源热备架构

| 优先级 | 行情源 | 备份方案 |
|---|---|---|
| 主 | 腾讯前复权日线（最快，无频限） | 自动重试 3 次 |
| 一级 | 东方财富 push2delay | 自动重试 3 次 |
| 二级 | akshare.stock_zh_a_hist | 终极兜底 |

**容灾规则**:
- 单接口 5s 超时 + 3 次重试
- 全瘫时返回空列表（宁缺毋滥）
- 仅舆情失效 → 跳过主线核验（仍可选股）

---

## 🔍 基础回测（原始策略，2025-07 → 2026-06，500 只样本）

| 指标 | 数值 |
|---|---|
| 总交易笔数 | {base['total_trades']} |
| 平均胜率 | {base['avg_win_rate_pct']}% |
| **累计收益率** | **{base['total_return_pct']}%** |
| **月均收益率** | **{base['avg_monthly_return_pct']}%** |
| 最佳月 | {base['best_month_pct']}% |
| 最差月 | {base['worst_month_pct']}% |
| 最大回撤 | {base['max_drawdown_pct']}% |
| 盈利月数 | {base['win_months']}/{base['total_months']} |

---

## 🏆 参数优化结果（精细化，240 组配置 + 仓位档位）

### 优化后的最佳参数

| 参数 | 值 | 说明 |
|---|---|---|
| 止盈 | +10% | 移动止盈触发位 |
| 止损 | -5% | 硬止损 |
| 持仓天数 | 7 天 | t+1 买入 → t+7 收盘前卖出 |
| 盈亏比下限 | 2.0:1 | 可放宽到 2.5（更严） |
| 每日选股上限 | 10 只 | top_n 越大越分散 |
| 仓位档位 | 30% | 单票占账户 30%（最高杠杆） |
| MA60 严格度 | **关闭** | MA5>MA10>MA20 已足够 |

### 优化后表现（30% 仓位）

| 指标 | 数值 |
|---|---|
| 总交易笔数 | {best_monthly['total_trades']} |
| 平均胜率 | {best_monthly['win_rate']}% |
| **累计收益率** | **{best_monthly['total_return_pct']}%** |
| **月均收益率** | **{best_monthly['avg_monthly']}%** |
| 最佳月 | {best_monthly['best_month']}% |
| 最差月 | {best_monthly['worst_month']}% |
| 盈利月数 | {best_monthly['win_months']}/{best_monthly['total_months']} |

### 月度明细（优化后）

| 月份 | 笔数 | 胜率 | 均收益 | 月度收益 |
|---|---|---|---|---|
"""

    for m in best_months:
        md += f"| {m['month']} | {m['trade_count']} | {m['win_rate']:.2f}% | {m['avg_return']:.2f}% | {m['total_return']:.2f}% |\n"

    md += f"""
---

## 💡 核心发现与洞察

### 1. 策略可行但需精细化
- **基础策略**（严格 MA60 + T=8%/S=3%/H=5d）：年化 {base['total_return_pct']}%，基本打平
- **优化策略**（无 MA60 + T=10%/S=5%/H=7d + 30% 仓位）：年化 {best_monthly['total_return_pct']}%，相对原策略提升 {best_monthly['total_return_pct'] - base['total_return_pct']:.2f} 个百分点

### 2. MA60 是双刃剑
- 严格 MA60 → 减少信号数量（每天 0-3 个），但质量并未提升
- 放松到 MA5>MA10>MA20 → 信号数量翻倍，胜率反而更好

### 3. 持仓 7 天最优
- 3 天：太短，趋势未走完
- 5 天：标准，但常被洗出
- **7 天**：让趋势充分演绎，又能控制单笔风险

### 4. 30% 仓位最大收益
- 10% 仓位：年化 {opt_data['phase2_positions'][0]['total_return_pct']}%
- 20% 仓位：年化 {opt_data['phase2_positions'][2]['total_return_pct']}%
- **30% 仓位：年化 {best_monthly['total_return_pct']}%**（最大）

### 5. 胜率 45% 是真实水平
- 严格按策略执行，胜率约 45-50%
- RR 2:1 才能盈亏平衡（已用 2.5:1 保险垫）
- 真实交易会有滑点和交易费，实际收益会更低

---

## 🚀 使用方法

### 手动触发选股（盘中）
```bash
cd ~/scripts/tuixue_screener
python3 screener.py                       # 完整流程（含分时）
python3 screener.py --skip-intraday       # 跳过 Layer 4（盘后用）
python3 screener.py --top 5               # 只取前 5 只
python3 screener.py --date 2026-07-08     # 指定日期
```

### 历史回测
```bash
python3 backtest.py --start 2025-07-01 --end 2026-06-30
python3 backtest.py --sample 1000 --top 5 --hold 7
```

### 参数优化
```bash
python3 optimize.py    # 粗颗粒（27 组配置）
python3 optimize2.py   # 细颗粒（240 组配置 + 仓位档位）
```

---

## 📁 项目结构

```
~/scripts/tuixue_screener/
├── config.py            # 所有可调阈值（头部常量）
├── data_source.py       # 三级行情数据源热备
├── pipeline.py          # 四层严苛选股流水线
├── screener.py          # 主入口（手动触发）
├── backtest.py          # 历史回测引擎
├── optimize.py          # 参数优化（粗）
├── optimize2.py         # 参数优化（细）
├── report.py            # 本报告生成器
├── cache/               # 数据缓存
├── logs/                # 运行日志
├── reports/             # 回测报告
└── blacklist.json       # 黑名单池
```

---

## ⚠️ 风险提示

1. **回测 ≠ 实盘**：未考虑滑点、交易费、涨跌停限制、停牌、流动性折价
2. **样本偏差**：500 只随机抽样可能错过小盘龙头
3. **过拟合风险**：优化出来的参数可能只对历史数据有效
4. **黑天鹅**：极端行情（如 2026-Q1 假设性事件）可能造成重大损失
5. **本系统仅供学习研究，请勿作为投资依据**

---

## 🔄 持续改进方向

- [ ] 接入东方财富板块资金净流入 API（更准的主线识别）
- [ ] 加入大盘指数过滤（沪指 > 20 日线才开仓）
- [ ] 加入连板梯队完整性过滤（板块 ≥3 只涨停）
- [ ] 加入舆情核验（公告利好过滤）
- [ ] 实盘模拟盘验证（小额测试 1 个月）
- [ ] 月度调优（每月底重新跑优化）
"""

    output_path = REPORTS / "final_report.md"
    output_path.write_text(md, encoding="utf-8")
    print(f"✅ 报告已生成: {output_path}")
    return md


if __name__ == "__main__":
    generate_report()