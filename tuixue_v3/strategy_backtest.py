#!/usr/bin/env python3
"""
tuixue_v3/strategy_backtest.py
Ship 16/100 — 策略回测集成 (factor → combine → buy → sell → 统计)

设计:
- 给定历史日期范围 + 候选池 (每日筛选输出)
- 每交易日:
  1. 算每个候选的 FactorScore (mock 历史因子 or 调上游)
  2. StrategyCombiner.combine → 排序后的 StockPick 列表
  3. 用 KellyInputs (默认 win_rate=0.5 / vol_n=0.02) 计算仓位
  4. T+1 开盘买, hold N 日, 收盘卖
  5. 记录每笔 trade {date, code, buy, sell, ret, weight, exit_reason}
- 输出:
  - 总收益率 / 年化 / 最大回撤 / 胜率 / 盈亏比 / Sharpe
  - 月度表
  - 因子贡献分解 (各 factor × final_score 的相关性)
  - 风险事件 (回测期内触发的 risk_severity 分布)

降级: 上游因子失败 → factor=None → 该候选跳过 (不参与排序, 不影响已买持仓)

2026-08-02 Ship 16 — 10000 轮迭代 P2 第六步
"""
from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class BacktestTrade:
    """单笔回测交易"""
    date: str             # 选股日 T
    buy_date: str         # T+1
    sell_date: str        # T+1+N
    code: str
    weight: float         # 仓位占比 (0~1)
    buy_price: float
    sell_price: float
    ret: float            # 收益率 (扣手续费前)
    net_ret: float        # 扣手续费 + 滑点后
    factor_score: float   # 当时的 composite
    risk_severity: str    # 当时风控
    rank: int             # 当日排名


@dataclass
class BacktestResult:
    """回测整体输出"""
    start: str
    end: str
    initial_capital: float
    final_capital: float
    total_return: float            # (final-initial)/initial
    annualized: float              # 年化
    max_drawdown: float            # 最大回撤 (负值, -0.3 = -30%)
    sharpe: float                  # 年化 Sharpe (rf=0)
    win_rate: float                # 胜率
    avg_win: float                 # 平均盈利
    avg_loss: float                # 平均亏损 (正数表示亏损幅度)
    profit_factor: float           # 盈亏比
    n_trades: int
    n_skipped: int                 # 跳过候选数 (因子失败)
    trades: list[BacktestTrade] = field(default_factory=list)
    monthly: list[dict] = field(default_factory=list)
    risk_distribution: dict = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"回测 {self.start}~{self.end} "
            f"收益 {self.total_return:+.2%} "
            f"年化 {self.annualized:+.2%} "
            f"最大回撤 {self.max_drawdown:.2%} "
            f"Sharpe {self.sharpe:.2f} "
            f"胜率 {self.win_rate:.1%} "
            f"盈亏比 {self.profit_factor:.2f} "
            f"交易 {self.n_trades} 笔"
        )


# ═══════════════════════════════════════════════════════
# 价格源抽象 (用历史 K 线查 buy/sell)
# ═══════════════════════════════════════════════════════

PriceProvider = Callable[[str, str], Optional[float]]
#  (code, date_str) → close price or None


def _make_price_provider(
    kline_lookup: Callable[[str], dict[str, float]],
) -> PriceProvider:
    """把 K 线 dict 查表包装成 PriceProvider"""
    def provider(code: str, date_str: str) -> Optional[float]:
        kl_for_code = kline_lookup(code)
        if not kl_for_code:
            return None
        return kl_for_code.get(date_str)
    return provider


# ═══════════════════════════════════════════════════════
# 主回测循环
# ═══════════════════════════════════════════════════════

def run_strategy_backtest(
    *,
    trade_dates: list[str],                     # 可交易日列表 (升序)
    candidates_by_date: dict[str, list[str]],   # {date: [code, ...]}
    factor_provider: Callable[[str, str], float],  # (date, code) → composite (-1~1)
    kline_lookup: Callable[[str], dict[str, float]],  # code → {date: close}
    initial_capital: float = 100000.0,
    hold_days: int = 5,
    commission: float = 0.0003,                  # 单边万三
    slippage: float = 0.001,                     # 单边千一
    max_position_pct: float = 0.20,
    max_total_position: float = 0.80,           # 总仓位上限
    max_picks_per_day: int = 10,
    risk_free_rate: float = 0.025,               # 年化无风险利率 (Shapre 用)
) -> BacktestResult:
    """跑策略回测

    Args:
        trade_dates: 交易日列表
        candidates_by_date: 每个交易日候选池
        factor_provider: 因子得分函数, 失败抛异常 → 该候选跳过
        kline_lookup: K 线查表, code → {date: close}
        initial_capital: 初始资金
        hold_days: 持有 N 日
        commission/slippage: 成本模型
        max_position_pct: 单股最大仓位
        max_total_position: 总仓位上限
        max_picks_per_day: 每日最多选几只
        risk_free_rate: Sharpe 分子用的 rf

    Returns:
        BacktestResult 含交易明细 + 月度 + 风险分布
    """
    cash = initial_capital
    positions: dict[str, dict] = {}  # code → {shares, cost, buy_date, sell_date, weight, rank}
    trades: list[BacktestTrade] = []
    n_skipped = 0
    daily_equity: list[tuple[str, float]] = []  # 每日 equity 曲线
    risk_dist: dict[str, int] = {"block": 0, "warning": 0, "ok": 0, "skip": 0}
    price_provider = _make_price_provider(kline_lookup)

    for i, date in enumerate(trade_dates):
        # 1. 处理今日到期持仓 (开盘按昨天 sell_date 卖, 简化: 用 sell_date 收盘)
        to_close = [c for c, p in positions.items() if p["sell_date"] <= date]
        for code in to_close:
            pos = positions.pop(code)
            sell_px = price_provider(code, pos["sell_date"])
            if sell_px is None or sell_px <= 0:
                # 拿不到价 → 按成本价平仓 (不亏不赚)
                sell_px = pos["cost"]
            ret = (sell_px - pos["cost"]) / pos["cost"]
            net_ret = ret - 2 * (commission + slippage)
            cash += pos["shares"] * sell_px * (1 - commission - slippage)
            trades.append(BacktestTrade(
                date=pos["buy_date_signal"], buy_date=pos["buy_date"],
                sell_date=pos["sell_date"], code=code,
                weight=pos["weight"], buy_price=pos["cost"],
                sell_price=sell_px, ret=ret, net_ret=net_ret,
                factor_score=pos["factor"], risk_severity=pos["severity"],
                rank=pos["rank"],
            ))

        # 2. 算今日 equity (mark-to-market)
        market_value = cash
        for code, pos in positions.items():
            cur_px = price_provider(code, date) or pos["cost"]
            market_value += pos["shares"] * cur_px
        daily_equity.append((date, market_value))

        # 3. 选今日新仓
        if i + 1 >= len(trade_dates):
            continue  # 最后一交易日没法 T+1 买
        candidates = candidates_by_date.get(date, [])
        scored: list[tuple[float, str, str]] = []  # (composite, code, severity)
        for code in candidates:
            try:
                score = factor_provider(date, code)
                if score is None:
                    raise ValueError("factor None")
                scored.append((score, code, "ok"))
            except Exception as e:
                logger.debug("因子失败 %s/%s: %s", date, code, e)
                n_skipped += 1

        scored.sort(key=lambda x: x[0], reverse=True)
        picks = scored[:max_picks_per_day]

        # 4. 仓位分配: 等权 × Kelly (简化为 1/N 但 cap max_position_pct)
        n_new = len(picks)
        if n_new == 0:
            continue
        per_share_pct = min(max_position_pct, max_total_position / n_new)
        buy_date = trade_dates[i + 1]
        sell_idx = min(i + 1 + hold_days, len(trade_dates) - 1)
        sell_date = trade_dates[sell_idx]

        for rank, (score, code, sev) in enumerate(picks, 1):
            # 已经有同 code 持仓 → 跳过
            if code in positions:
                continue
            buy_px = price_provider(code, buy_date)
            if buy_px is None or buy_px <= 0:
                n_skipped += 1
                continue
            # 能用资金
            available_cash = cash * per_share_pct
            if available_cash < buy_px * 100:  # 1 手 = 100 股
                continue
            shares = int(available_cash / buy_px / 100) * 100
            if shares == 0:
                continue
            cost = shares * buy_px * (1 + commission + slippage)
            if cost > cash:
                continue
            cash -= cost
            positions[code] = {
                "shares": shares, "cost": buy_px,
                "buy_date": buy_date, "sell_date": sell_date,
                "buy_date_signal": date, "weight": per_share_pct,
                "factor": score, "severity": sev, "rank": rank,
            }
            risk_dist[sev] = risk_dist.get(sev, 0) + 1

    # 收尾: 平掉所有剩余持仓
    last_date = trade_dates[-1]
    for code, pos in positions.items():
        sell_px = price_provider(code, last_date) or pos["cost"]
        ret = (sell_px - pos["cost"]) / pos["cost"]
        net_ret = ret - 2 * (commission + slippage)
        cash += pos["shares"] * sell_px * (1 - commission - slippage)
        trades.append(BacktestTrade(
            date=pos["buy_date_signal"], buy_date=pos["buy_date"],
            sell_date=last_date, code=code,
            weight=pos["weight"], buy_price=pos["cost"],
            sell_price=sell_px, ret=ret, net_ret=net_ret,
            factor_score=pos["factor"], risk_severity=pos["severity"],
            rank=pos["rank"],
        ))

    # ═══════════════════════════════════════════════════════
    # 统计
    # ═══════════════════════════════════════════════════════
    total_return = (cash - initial_capital) / initial_capital
    n_days = max(1, len(daily_equity))
    annualized = (1 + total_return) ** (252 / n_days) - 1 if n_days > 1 else total_return
    max_dd = _compute_max_drawdown([eq for _, eq in daily_equity])
    sharpe = _compute_sharpe([eq for _, eq in daily_equity], rf=risk_free_rate)
    wins = [t.net_ret for t in trades if t.net_ret > 0]
    losses = [t.net_ret for t in trades if t.net_ret <= 0]
    win_rate = len(wins) / len(trades) if trades else 0.0
    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = abs(statistics.mean(losses)) if losses else 0.0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else 0.0
    monthly = _compute_monthly_pnl(trades, initial_capital, daily_equity)

    return BacktestResult(
        start=trade_dates[0] if trade_dates else "",
        end=trade_dates[-1] if trade_dates else "",
        initial_capital=initial_capital,
        final_capital=cash,
        total_return=total_return,
        annualized=annualized,
        max_drawdown=max_dd,
        sharpe=sharpe,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        n_trades=len(trades),
        n_skipped=n_skipped,
        trades=trades,
        monthly=monthly,
        risk_distribution=risk_dist,
    )


# ═══════════════════════════════════════════════════════
# 统计工具
# ═══════════════════════════════════════════════════════

def _compute_max_drawdown(equity: list[float]) -> float:
    """最大回撤 (负值, -0.3 = -30%)"""
    if not equity:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (v - peak) / peak
        if dd < max_dd:
            max_dd = dd
    return max_dd


def _compute_sharpe(equity: list[float], rf: float = 0.025) -> float:
    """年化 Sharpe (rf 年化)"""
    if len(equity) < 2:
        return 0.0
    rets = []
    for i in range(1, len(equity)):
        if equity[i - 1] > 0:
            rets.append((equity[i] - equity[i - 1]) / equity[i - 1])
    if not rets:
        return 0.0
    mean_d = statistics.mean(rets)
    std_d = statistics.pstdev(rets) or 1e-9
    rf_d = rf / 252
    return (mean_d - rf_d) / std_d * math.sqrt(252)


def _compute_monthly_pnl(
    trades: list[BacktestTrade],
    initial: float,
    equity_curve: list[tuple[str, float]],
) -> list[dict]:
    """按月汇总 PnL"""
    if not equity_curve:
        return []
    monthly = []
    cur_month = None
    start_eq = initial
    for date_str, eq in equity_curve:
        ym = date_str[:7]  # YYYY-MM
        if ym != cur_month:
            if cur_month is not None:
                monthly.append({
                    "month": cur_month,
                    "end_equity": prev_eq,
                    "return": (prev_eq - start_eq) / start_eq,
                })
            cur_month = ym
            start_eq = prev_eq if monthly else initial
        prev_eq = eq
    if cur_month:
        monthly.append({
            "month": cur_month,
            "end_equity": prev_eq,
            "return": (prev_eq - start_eq) / start_eq,
        })
    return monthly
