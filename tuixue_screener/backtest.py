#!/usr/bin/env python3
"""
tuixue_screener/backtest.py
历史回测引擎：逐日重放四层选股流水线，统计月度收益率。

回测逻辑：
- 数据范围：2025-07-01 至 2026-06-30（一年 12 个月）
- 每个交易日 t 触发选股（模拟当日 10:30 决策）
- t+1 开盘价买入（避开未来函数）
- 持仓 N 天（默认 5 日），按风控规则平仓
- 月度收益 = Σ(每月每笔交易盈亏) / 账户资金
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

import data_source as ds
import pipeline as P
import screener as S
import config as C

log = logging.getLogger("backtest")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ROOT = Path(__file__).parent
CACHE = ROOT / C.CACHE_DIR_NAME

# ════════════════════════════════════════════════════════════
# 历史数据获取（一次性预加载，缓存到本地）
# ════════════════════════════════════════════════════════════
def fetch_history_klines(codes: list[str], start: str, end: str,
                          force_refresh: bool = False,
                          max_workers: int = 4) -> dict[str, pd.DataFrame]:
    """
    批量获取历史 K 线（并发，带重试 + 退避）。
    返回 {code: DataFrame[date, open, close, high, low, volume, amount, change_pct]}
    """
    cache_file = CACHE / f"history_{start}_{end}.json"
    if cache_file.exists() and not force_refresh:
        try:
            raw = json.loads(cache_file.read_text())
            out = {}
            for code, data in raw.items():
                df = pd.DataFrame(data)
                if "date" in df.columns:
                    df["date"] = pd.to_datetime(df["date"])
                    out[code] = df.reset_index(drop=True)
            log.info(f"从缓存加载历史: {len(out)} 只")
            return out
        except Exception as e:
            log.warning(f"缓存读取失败: {e}")

    out = {}
    completed = [0]
    failed = [0]

    def _fetch_one(code: str) -> tuple[str, pd.DataFrame | None]:
        # 3 次重试
        for attempt in range(3):
            try:
                klines, src = ds.get_kline(code, 250)
                if not klines:
                    return code, None
                rows = [P._parse_kline_row(k) for k in klines]
                rows = [r for r in rows if r is not None]
                if len(rows) < 30:
                    return code, None
                df = pd.DataFrame(rows)
                if "date" not in df.columns:
                    return code, None
                df["date"] = pd.to_datetime(df["date"])
                df = df[(df["date"] >= pd.to_datetime(start)) &
                        (df["date"] <= pd.to_datetime(end))]
                if df.empty:
                    return code, None
                return code, df.reset_index(drop=True)
            except Exception as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                continue
        failed[0] += 1
        return code, None

    # 限流：4 线程（避免 EM 触发限流）
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, code): code for code in codes}
        for fut in as_completed(futures):
            code, df = fut.result()
            completed[0] += 1
            if completed[0] % 50 == 0:
                log.info(f"  进度: {completed[0]}/{len(codes)} (成功 {len(out)}, 失败 {failed[0]})")
            if df is not None:
                out[code] = df

    # 保存缓存
    cache_file.write_text(json.dumps(
        {code: df.to_dict("records") for code, df in out.items()},
        default=str, ensure_ascii=False
    ))
    log.info(f"已缓存历史: {len(out)} 只 (失败 {failed[0]})")
    return out

def fetch_history_spot(date_str: str) -> list[dict]:
    """获取某日全市场快照（用于回测当日过滤）"""
    # akshare 历史快照：akshare.stock_zh_a_hist 不直接给历史快照
    # 用 stock_zh_a_spot_em 当日 + 历史 K 线最后一行模拟（精度有限）
    # 更准的方案：akshare.stock_zh_a_daily（已废弃）或 自建
    # 简化：用 K 线当日收盘价作为"当日快照"，遍历全代码
    return []

def fetch_all_a_codes() -> list[str]:
    """获取沪深主板全部代码列表"""
    spot, src = ds.get_spot()
    if not spot:
        return []
    codes = []
    for r in spot:
        code = str(r.get("f12", "")).zfill(6)
        # 仅沪深主板
        if code.startswith(("60", "603", "605", "000", "002", "003")):
            name = r.get("f14", "")
            if not any(k in name for k in C.EXCLUDE_KEYWORDS):
                codes.append(code)
    return codes

# ════════════════════════════════════════════════════════════
# 历史板块数据（重放用）
# ════════════════════════════════════════════════════════════
def fetch_history_sectors(start: str, end: str) -> dict[str, list[dict]]:
    """
    历史板块涨幅数据。
    akshare: ak.stock_board_industry_index_em（仅当前）
    历史化方案：ak.stock_board_industry_hist_em 单板块历史 → 推断当日涨幅
    简化：仅依赖当日 spot + 阈值变化模拟（精度有限）
    """
    # 实际生产可接入 akshare.stock_board_industry_hist_min_em
    # 这里只缓存当日
    sectors, src = ds.get_sector_rank()
    if sectors:
        return {datetime.now().strftime("%Y-%m-%d"): sectors}
    return {}

def fetch_history_zt_pool(start: str, end: str) -> dict[str, list[dict]]:
    """历史涨停池（重放用）"""
    # 简化：仅当日
    today = datetime.now().strftime("%Y-%m-%d")
    zt, src = ds.get_zt_pool(today)
    return {today: zt} if zt else {}

# ════════════════════════════════════════════════════════════
# 历史回测（精简版：仅复盘 4 层策略，不重放分时）
# ════════════════════════════════════════════════════════════
def backtest_simplified(start: str = "2025-07-01",
                         end: str = "2026-06-30",
                         hold_days: int = 5,
                         top_n: int = 3,
                         sample_n: int = 300,
                         initial_cash: float = 100000) -> dict:
    """
    简化回测：
    - 从 sample_n 只主板票池中，模拟每个交易日的选股信号
    - t+1 开盘买入，持有 hold_days
    - 月度收益统计
    """
    log.info(f"\n回测参数: {start} → {end}, 持仓 {hold_days} 天, 取前 {top_n}")
    log.info(f"初始资金: {initial_cash:,.0f}")

    # 1. 拉取全 A 代码
    all_codes = fetch_all_a_codes()
    log.info(f"沪深主板票池: {len(all_codes)} 只")
    if len(all_codes) > sample_n:
        # 随机抽样（保持代表性）
        import random
        random.seed(42)
        all_codes = random.sample(all_codes, sample_n)
        log.info(f"抽样后: {len(all_codes)} 只")

    # 2. 拉取历史 K 线（一次性：含回测前 80 天用于 MA60）
    # 拉取窗口：start - 100 天 → end
    from datetime import datetime, timedelta
    pre_start = (pd.to_datetime(start) - timedelta(days=120)).strftime("%Y-%m-%d")
    log.info(f"拉取窗口: {pre_start} → {end}（含 MA60 预备数据）")
    history = fetch_history_klines(all_codes, pre_start, end)
    log.info(f"实际拿到历史: {len(history)} 只")

    # 3. 模拟每日选股 → 交易
    trades = []
    rejected_dates = 0

    # 获取所有交易日（从 history 数据中提取，仅取回测区间）
    all_dates = set()
    for df in history.values():
        if not df.empty:
            mask = (df["date"] >= pd.to_datetime(start)) & (df["date"] <= pd.to_datetime(end))
            all_dates.update(df.loc[mask, "date"].dt.strftime("%Y-%m-%d").tolist())
    trade_dates = sorted(all_dates)
    log.info(f"回测区间内交易日数: {len(trade_dates)}")

    for i, t_date in enumerate(trade_dates[:-hold_days]):
        # 当日 K 线（用来生成信号）
        # 这里用 "t 日收盘价作为买入信号 → t+1 开盘价成交" 模拟

        # 4 层选股（在 t 日数据上）
        candidates_today = []

        for code, df in history.items():
            row_t = df[df["date"] == pd.to_datetime(t_date)]
            if row_t.empty:
                continue
            row_t = row_t.iloc[0]

            # 复盘 Layer 3（日线）
            df_until_t = df[df["date"] <= pd.to_datetime(t_date)].tail(80)
            if len(df_until_t) < 65:
                continue
            closes = df_until_t["close"].tolist()

            ma5 = sum(closes[-5:]) / 5
            ma10 = sum(closes[-10:]) / 10
            ma20 = sum(closes[-20:]) / 20
            ma60 = sum(closes[-60:]) / 60

            if not (ma5 > ma10 > ma20 > ma60):
                continue
            if row_t["close"] < ma5:
                continue

            gain_20 = (closes[-1] / closes[-20] - 1) * 100
            if gain_20 >= C.PHASE_GAIN_MAX:
                continue

            turnover = row_t.get("turnover_rate", 0) or 0
            # 换手率过滤：仅当有数据时启用（tencent 不返回换手率）
            if turnover > 0:
                if not (C.TURNOVER_RATE_MIN <= turnover <= C.TURNOVER_RATE_HARD_MAX):
                    continue

            amount = row_t.get("amount", 0) or 0
            if amount > 0 and amount < C.MIN_TURNOVER_YUAN:
                continue

            # 量价：最近 5 日均量 vs 前 20 日均量
            vols = df_until_t["volume"].tolist()
            if len(vols) >= 25:
                recent_5 = sum(vols[-5:]) / 5
                rise_15 = sum(vols[-20:-5]) / 15
                if recent_5 > rise_15 * 3:
                    continue

            # 盈亏比（固定 +8% / -3% 模型）
            entry = row_t["close"]
            target = entry * 1.08
            stop = entry * 0.97
            upside = target - entry
            downside = entry - stop
            if downside <= 0:
                continue
            rr = upside / downside
            if rr < C.MIN_RR_RATIO:
                continue

            candidates_today.append({
                "code": code,
                "entry_price": entry,
                "target_price": target,
                "stop_price": stop,
                "rr": rr,
                "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
                "change_pct": row_t.get("change_pct", 0),
            })

        # 排序取前 N
        candidates_today.sort(key=lambda x: x["rr"], reverse=True)
        picks = candidates_today[:top_n]

        if not picks:
            rejected_dates += 1
            continue

        # 模拟买入（t+1 开盘）→ 持有 hold_days → 收盘卖出
        for pick in picks:
            code = pick["code"]
            df = history[code]
            df_after = df[df["date"] > pd.to_datetime(t_date)].reset_index(drop=True)
            if len(df_after) < hold_days:
                continue

            buy_row = df_after.iloc[0]   # t+1
            sell_row = df_after.iloc[hold_days - 1]   # t+hold

            buy_price = buy_row["open"]   # 开盘价
            sell_price = sell_row["close"]  # 收盘价

            # 模拟止损（持仓期内是否触发）
            for j in range(hold_days):
                row_j = df_after.iloc[j]
                # 移动止盈：盈利 ≥ 6% 后从最高点回落 ≥ 3% 卖出
                if j >= 2:
                    high_so_far = df_after.iloc[:j+1]["high"].max()
                    if buy_price > 0:
                        profit_pct = (high_so_far / buy_price - 1) * 100
                        if profit_pct >= C.TRAILING_ACTIVATION_PCT:
                            drawdown = (high_so_far - row_j["close"]) / high_so_far * 100
                            if drawdown >= C.TRAILING_PULLBACK_PCT:
                                sell_price = row_j["close"]
                                sell_date = row_j["date"]
                                break
                # MA5 止损
                closes_j = df_after.iloc[:j+1]["close"].tolist()
                if len(closes_j) >= 5:
                    ma5_j = sum(closes_j[-5:]) / 5
                    if row_j["close"] < ma5_j:
                        sell_price = row_j["close"]
                        sell_date = row_j["date"]
                        break
            else:
                sell_date = sell_row["date"]

            ret_pct = (sell_price / buy_price - 1) * 100
            trades.append({
                "code": code,
                "buy_date": str(buy_row["date"])[:10],
                "sell_date": str(sell_date)[:10],
                "buy_price": round(buy_price, 2),
                "sell_price": round(sell_price, 2),
                "ret_pct": round(ret_pct, 2),
                "month": str(sell_date)[:7],  # YYYY-MM
            })

    log.info(f"\n回测完成: 总交易 {len(trades)} 笔, 无信号日 {rejected_dates}")
    return {
        "trades": trades,
        "rejected_dates": rejected_dates,
        "trade_dates_count": len(trade_dates),
        "params": {
            "start": start, "end": end,
            "hold_days": hold_days, "top_n": top_n,
            "sample_n": sample_n, "initial_cash": initial_cash,
        },
    }

# ════════════════════════════════════════════════════════════
# 月度收益统计
# ════════════════════════════════════════════════════════════
def monthly_returns(bt_result: dict, single_position_pct: float = 0.20) -> dict:
    """
    月度收益统计：
    - 单票仓位 20%
    - 月度收益 = 该月所有交易平均收益 × 仓位
    """
    trades = bt_result["trades"]
    if not trades:
        return {
            "months": [],
            "summary": {
                "total_trades": 0, "total_months": 0,
                "win_months": 0, "win_month_rate": 0,
                "total_return_pct": 0, "avg_monthly_return_pct": 0,
                "best_month_pct": 0, "worst_month_pct": 0,
                "avg_win_rate_pct": 0, "max_drawdown_pct": 0,
            }
        }

    df = pd.DataFrame(trades)

    # 单笔收益 → 仓位收益
    df["position_return"] = df["ret_pct"] * single_position_pct / 100

    # 月度聚合
    monthly = df.groupby("month").agg(
        trade_count=("ret_pct", "count"),
        win_rate=("ret_pct", lambda x: (x > 0).sum() / len(x) * 100),
        avg_return=("ret_pct", "mean"),
        total_return=("position_return", "sum"),
        best=("ret_pct", "max"),
        worst=("ret_pct", "min"),
    ).round(2)

    # 累计收益（复利）
    monthly["cumulative_return"] = ((1 + monthly["total_return"] / 100).cumprod() - 1) * 100

    # 总体统计
    total_return = (1 + monthly["total_return"] / 100).prod() - 1
    total_months = len(monthly)
    win_months = sum(1 for r in monthly["total_return"] if r > 0)

    summary = {
        "total_trades": len(trades),
        "total_months": total_months,
        "win_months": win_months,
        "win_month_rate": round(win_months / total_months * 100, 2) if total_months else 0,
        "total_return_pct": round(total_return * 100, 2),
        "avg_monthly_return_pct": round(monthly["total_return"].mean(), 2),
        "best_month_pct": round(monthly["total_return"].max(), 2),
        "worst_month_pct": round(monthly["total_return"].min(), 2),
        "avg_win_rate_pct": round(monthly["win_rate"].mean(), 2),
        "max_drawdown_pct": round(_max_drawdown(monthly["total_return"].tolist()), 2),
    }

    return {
        "months": monthly.reset_index().to_dict("records"),
        "summary": summary,
    }

def _max_drawdown(returns: list[float]) -> float:
    """最大回撤（百分比）"""
    if not returns:
        return 0
    cumulative = []
    cum = 1.0
    for r in returns:
        cum *= (1 + r / 100)
        cumulative.append(cum)
    peak = cumulative[0]
    max_dd = 0
    for v in cumulative:
        if v > peak:
            peak = v
        dd = (v / peak - 1) * 100
        if dd < max_dd:
            max_dd = dd
    return max_dd

# ════════════════════════════════════════════════════════════
# 报告输出
# ════════════════════════════════════════════════════════════
def print_report(bt_result: dict, monthly_result: dict):
    """打印回测报告"""
    print(f"\n{'='*60}")
    print(f"📊 退学战法回测报告")
    print(f"{'='*60}")

    params = bt_result["params"]
    print(f"\n回测区间: {params['start']} → {params['end']}")
    print(f"持仓周期: {params['hold_days']} 天")
    print(f"每日选股上限: {params['top_n']} 只")
    print(f"样本池: {params['sample_n']} 只主板票")
    print(f"交易日总数: {bt_result['trade_dates_count']}")
    print(f"无信号日: {bt_result['rejected_dates']}")

    print(f"\n{'─'*60}")
    print("📈 总体表现")
    print(f"{'─'*60}")
    summary = monthly_result["summary"]
    print(f"  总交易笔数:       {summary['total_trades']}")
    print(f"  总月数:           {summary['total_months']}")
    print(f"  盈利月数:         {summary['win_months']} ({summary['win_month_rate']}%)")
    print(f"  累计收益率:       {summary['total_return_pct']:.2f}%")
    print(f"  月均收益率:       {summary['avg_monthly_return_pct']:.2f}%")
    print(f"  最佳月:           {summary['best_month_pct']:.2f}%")
    print(f"  最差月:           {summary['worst_month_pct']:.2f}%")
    print(f"  平均胜率:         {summary['avg_win_rate_pct']:.2f}%")
    print(f"  最大回撤:         {summary['max_drawdown_pct']:.2f}%")

    print(f"\n{'─'*60}")
    print("📅 月度明细")
    print(f"{'─'*60}")
    print(f"{'月份':<10} {'笔数':>5} {'胜率%':>7} {'均收益%':>9} {'月度收益%':>11} {'累计%':>10}")
    print(f"{'─'*60}")
    for m in monthly_result["months"]:
        print(f"{m['month']:<10} {m['trade_count']:>5} {m['win_rate']:>7.2f} "
              f"{m['avg_return']:>9.2f} {m['total_return']:>11.2f} {m['cumulative_return']:>10.2f}")

# ════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════
def main():
    import argparse
    parser = argparse.ArgumentParser(description="退学战法历史回测")
    parser.add_argument("--start", default="2025-07-01")
    parser.add_argument("--end", default="2026-06-30")
    parser.add_argument("--hold", type=int, default=5)
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--sample", type=int, default=300)
    parser.add_argument("--cash", type=float, default=100000)
    parser.add_argument("--position", type=float, default=0.20, help="单票仓位")
    parser.add_argument("--output", default="reports/backtest_report.json")
    args = parser.parse_args()

    bt_result = backtest_simplified(
        start=args.start, end=args.end,
        hold_days=args.hold, top_n=args.top,
        sample_n=args.sample, initial_cash=args.cash
    )
    monthly_result = monthly_returns(bt_result, args.position)

    print_report(bt_result, monthly_result)

    # 保存
    output = {
        "backtest": bt_result,
        "monthly": monthly_result,
        "generated_at": datetime.now().isoformat(),
    }
    output_path = ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    print(f"\n✅ 报告已保存: {output_path}")

if __name__ == "__main__":
    main()