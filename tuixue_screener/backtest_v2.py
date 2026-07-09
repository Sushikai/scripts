#!/usr/bin/env python3
"""
tuixue_screener/backtest_v2.py
方案 C 回测引擎：大盘择时 + 周期判定 + 真实仓位 + 手续费滑点。

核心改造（相对 v1）：
1. 大盘择时：沪深300 > MA20 才允许开仓（沪指 MA60 趋势判定周期）
2. 周期判定：基于大盘 MA20/MA60 斜率 + 涨幅榜（冰点/启动/高潮/退潮）
3. 真实仓位模型：账户总资金约束，最多持仓 N 只（不重复计算）
4. 交易成本：双边佣金 0.05% + 印花税 0.1%（卖出）+ 滑点 0.2%
5. 主线识别：用板块成分股交集（已缓存所有板块代码）
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import data_source as ds
import config as C

log = logging.getLogger("backtest_v2")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ROOT = Path(__file__).parent
CACHE = ROOT / "cache"

# ════════════════════════════════════════════════════════════
# 交易成本
# ════════════════════════════════════════════════════════════
COMMISSION_PCT = 0.0005      # 双边佣金 0.05%
STAMP_TAX_PCT = 0.001        # 印花税 0.1%（仅卖出）
SLIPPAGE_PCT = 0.002         # 滑点 0.2%（买卖各算一次）
ROUND_TRIP_COST = COMMISSION_PCT * 2 + STAMP_TAX_PCT + SLIPPAGE_PCT * 2
# 总成本约 0.65% 单边


# ════════════════════════════════════════════════════════════
# 沪深300 指数 K 线（大盘择时核心）
# ════════════════════════════════════════════════════════════
def fetch_index_kline(code: str = "1.000300", days: int = 500) -> list | None:
    """获取沪深300指数 K 线（push2）"""
    cache_name = f"index_kline_{code}_{days}"
    cache_path = CACHE / f"{cache_name}.json"
    if cache_path.exists():
        try:
            d = json.loads(cache_path.read_text())
            if time.time() - d.get("ts", 0) < 7 * 86400:
                return d.get("data")
        except Exception:
            pass

    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": code,
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": 101, "fqt": 1, "end": "20500101", "lmt": days,
    }
    last_err = ""
    for attempt in range(5):
        try:
            r = requests.get(url, params=params, timeout=15,
                             headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
            r.raise_for_status()
            d = r.json()
            klines_raw = (d.get("data") or {}).get("klines", []) or []
            if not klines_raw:
                return None
            cache_path.write_text(json.dumps({"ts": time.time(), "data": klines_raw}, ensure_ascii=False))
            return klines_raw
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:60]}"
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
    log.warning(f"指数 {code} 拉取失败: {last_err}")
    return None


# ════════════════════════════════════════════════════════════
# 历史板块涨幅榜（用 push2 BK 一次拉每日快照）
# 实际上 push2 不支持历史快照，只能用：当日 push2 BK 涨幅榜
# 解决：用"个股收盘涨幅反推板块热度"（间接法）
# ════════════════════════════════════════════════════════════
def compute_daily_breadth(history: dict, trade_dates: list[str]) -> dict[str, dict]:
    """
    每日市场宽度（替代板块数据）：
    - up_count: 上涨家数
    - limit_up_count: 涨停家数（涨幅 ≥ 9.5%）
    - strong_count: 强势股数（涨幅 3-8%）
    - breadth_pct: 上涨家数 / 总样本
    """
    breadth = {}
    for t_date in trade_dates:
        up = 0
        limit_up = 0
        strong = 0
        total = 0
        for code, df in history.items():
            if df.empty:
                continue
            row_t = df[df["date"] == pd.to_datetime(t_date)]
            if row_t.empty:
                continue
            chg = row_t.iloc[0].get("change_pct", 0) or 0
            total += 1
            if chg > 0:
                up += 1
            if chg >= 9.5:
                limit_up += 1
            if 3 <= chg <= 8:
                strong += 1
        breadth[t_date] = {
            "up_count": up,
            "limit_up_count": limit_up,
            "strong_count": strong,
            "total": total,
            "breadth_pct": up / max(1, total),
        }
    return breadth


# ════════════════════════════════════════════════════════════
# 周期判定（基于大盘 + 市场宽度）
# ════════════════════════════════════════════════════════════
def check_market_regime(index_df: pd.DataFrame, t_date: str, breadth: dict) -> dict:
    """
    判定当日市场周期。
    返回: {phase: 冰点修复/启动确认/高潮/退潮/中性, allow: bool, detail: str}
    """
    row_t = index_df[index_df["date"] == pd.to_datetime(t_date)]
    if row_t.empty:
        return {"phase": "中性", "allow": False, "detail": "无指数数据"}

    row_t = row_t.iloc[0]
    close = row_t["close"]
    ma20 = row_t.get("ma20", np.nan)
    ma60 = row_t.get("ma60", np.nan)
    ma20_slope = row_t.get("ma20_slope", np.nan)  # 5 日斜率

    bd = breadth.get(t_date, {})
    limit_up = bd.get("limit_up_count", 0)
    breadth_pct = bd.get("breadth_pct", 0)

    # 1. 大盘择时（核心闸门）：收盘 > MA20
    if np.isnan(ma20) or close < ma20:
        return {"phase": "退潮", "allow": False,
                "detail": f"沪深300 ({close:.0f}) < MA20 ({ma20:.0f})",
                "limit_up": limit_up, "breadth_pct": breadth_pct}

    # 2. 涨停数判定
    if limit_up < 25:
        return {"phase": "冰点修复", "allow": True,
                "detail": f"涨停仅 {limit_up} 家, 大盘 OK（{close:.0f} > MA20 {ma20:.0f}）",
                "limit_up": limit_up, "breadth_pct": breadth_pct}

    if limit_up > 80:
        return {"phase": "高潮", "allow": True, "caution": True,
                "detail": f"涨停 {limit_up} 家（高潮，警惕见顶）",
                "limit_up": limit_up, "breadth_pct": breadth_pct}

    # 启动确认
    return {"phase": "启动确认", "allow": True,
            "detail": f"涨停 {limit_up} 家, 大盘 OK, breadth {breadth_pct:.1%}",
            "limit_up": limit_up, "breadth_pct": breadth_pct}


# ════════════════════════════════════════════════════════════
# 主回测引擎
# ════════════════════════════════════════════════════════════
def backtest_v2(start: str = "2025-07-01",
                end: str = "2026-06-30",
                hold_days: int = 7,
                top_n: int = 5,
                sample_n: int = 500,
                initial_cash: float = 100000,
                max_positions: int = 5,
                position_pct: float = 0.20,
                target_pct: float = 0.10,
                stop_pct: float = 0.05,
                min_rr: float = 2.0,
                use_ma60: bool = False,
                _shared_data: dict = None) -> dict:
    """
    回测 v2：大盘择时 + 真实仓位 + 交易成本。
    """
    log.info(f"\n=== 方案 C 回测 ===")
    log.info(f"区间: {start} → {end}, 持仓 {hold_days} 天, top_n={top_n}")
    log.info(f"初始资金: {initial_cash:,.0f}, 最大持仓 {max_positions} 只, 单仓 {position_pct*100:.0f}%")

    # 1. 沪深300 指数
    log.info("\n[Step 1] 加载沪深300 指数")
    klines_raw = fetch_index_kline("1.000300", 500)
    if not klines_raw:
        log.error("指数数据加载失败")
        return {}
    rows = []
    for line in klines_raw:
        parts = line.split(",")
        if len(parts) < 11:
            continue
        rows.append({
            "date": pd.to_datetime(parts[0]),
            "open": float(parts[1]), "close": float(parts[2]),
            "high": float(parts[3]), "low": float(parts[4]),
            "volume": float(parts[5]), "amount": float(parts[6]),
            "change_pct": float(parts[8]),
        })
    index_df = pd.DataFrame(rows)
    index_df["ma20"] = index_df["close"].rolling(20).mean()
    index_df["ma60"] = index_df["close"].rolling(60).mean()
    index_df["ma20_slope"] = (index_df["ma20"] - index_df["ma20"].shift(5)) / 5
    log.info(f"  指数数据: {len(index_df)} 条 ({index_df['date'].iloc[0]} → {index_df['date'].iloc[-1]})")

    # 2. 个股历史
    log.info("\n[Step 2] 加载个股历史")
    all_codes = []
    spot, _ = ds.get_spot()
    if spot:
        for r in spot:
            code = str(r.get("f12", "")).zfill(6)
            if code.startswith(("60", "603", "605", "000", "002", "003")):
                name = r.get("f14", "")
                if not any(k in name for k in C.EXCLUDE_KEYWORDS):
                    all_codes.append(code)
    log.info(f"沪深主板票池: {len(all_codes)} 只")

    import random
    random.seed(42)
    if len(all_codes) > sample_n:
        sample = random.sample(all_codes, sample_n)
    else:
        sample = all_codes

    # 复用 v1 的 fetch
    from backtest import fetch_history_klines
    pre_start = (pd.to_datetime(start) - timedelta(days=120)).strftime("%Y-%m-%d")

    if _shared_data is not None and _shared_data.get("sample") == sample_n and _shared_data.get("start") == start and _shared_data.get("end") == end:
        history = _shared_data["history"]
        index_df = _shared_data["index_df"]
        trade_dates = _shared_data["trade_dates"]
        breadth = _shared_data["breadth"]
        log.info(f"  共享数据: history={len(history)} 只")
    else:
        history = fetch_history_klines(sample, pre_start, end)
        log.info(f"  个股历史: {len(history)} 只")

    # 3. 计算每日市场宽度
    log.info("\n[Step 3] 计算市场宽度")
    all_dates = set()
    for df in history.values():
        if not df.empty:
            mask = (df["date"] >= pd.to_datetime(start)) & (df["date"] <= pd.to_datetime(end))
            all_dates.update(df.loc[mask, "date"].dt.strftime("%Y-%m-%d").tolist())
    # 加上指数的交易日（更准）
    idx_dates = index_df[(index_df["date"] >= pd.to_datetime(start)) &
                         (index_df["date"] <= pd.to_datetime(end))]["date"].dt.strftime("%Y-%m-%d").tolist()
    trade_dates = sorted(set(all_dates) & set(idx_dates))
    log.info(f"  交易日: {len(trade_dates)}")

    if _shared_data is not None and "breadth" in _shared_data and _shared_data.get("trade_dates") == trade_dates:
        breadth = _shared_data["breadth"]
        log.info(f"  共享宽度")
    else:
        breadth = compute_daily_breadth(history, trade_dates)
        log.info(f"  重新计算宽度")
    avg_zdt = sum(b["limit_up_count"] for b in breadth.values()) / max(1, len(breadth))
    avg_brd = sum(b["breadth_pct"] for b in breadth.values()) / max(1, len(breadth))
    log.info(f"  平均涨停: {avg_zdt:.1f} 家/日, 平均 breadth: {avg_brd:.1%}")

    # 4. 预计算个股指标（pandas 向量化 + 多线程）
    log.info("\n[Step 4] 预计算技术指标（pandas 向量化）")
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _precompute_one(code, df):
        if df.empty or len(df) < 25:
            return code, None
        closes = df["close"].values
        n = len(closes)
        if n < (65 if use_ma60 else 25):
            return code, None

        # pandas rolling（底层 C 优化，比 Python 循环快 10x+）
        # 用 min_periods=window：未满窗口时为 NaN（避免前几日假信号）
        s = df["close"]
        ma5 = s.rolling(5, min_periods=5).mean().values
        ma10 = s.rolling(10, min_periods=10).mean().values
        ma20 = s.rolling(20, min_periods=20).mean().values
        ma60 = s.rolling(60, min_periods=60).mean().values if use_ma60 else None

        # 20 日累计涨幅
        gain_20 = s.pct_change(20).values * 100

        # 量价
        v = df["volume"] if "volume" in df.columns else pd.Series(np.zeros(n))
        vol_5 = v.rolling(5, min_periods=5).mean().values
        vol_15 = v.rolling(15, min_periods=15).mean().values

        result = {
            "dates": df["date"].values,
            "closes": closes,
            "opens": df["open"].values,
            "highs": df["high"].values,
            "volumes": v.values,
            "amounts": df["amount"].values if "amount" in df.columns else np.zeros(n),
            "ma5": ma5, "ma10": ma10, "ma20": ma20,
            "gain_20": gain_20, "vol_5": vol_5, "vol_15": vol_15,
            # 加速：日期字符串 → 索引的 O(1) 映射
            "date_to_idx": {str(d)[:10]: i for i, d in enumerate(df["date"].values)},
        }
        if use_ma60:
            result["ma60"] = ma60
        return code, result

    indicators = {}
    if _shared_data is not None and "indicators" in _shared_data:
        indicators = _shared_data["indicators"]
        log.info(f"  共享指标: {len(indicators)} 只")
    else:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_precompute_one, c, df): c for c, df in history.items()}
            for fut in as_completed(futures):
                code, result = fut.result()
                if result is not None:
                    indicators[code] = result
        log.info(f"  预计算完成: {len(indicators)} 只")

    # 5. 逐日回测（含真实仓位 + 手续费）
    log.info("\n[Step 5] 逐日回测")
    cash = initial_cash
    positions = {}  # {code: {buy_price, shares, buy_date, stop_loss}}
    closed_trades = []  # 已平仓交易
    daily_log = []  # 每日账户净值

    target_total_pos = position_pct * max_positions  # 账户总仓位 = 单票仓位 × 最大持仓数
    log.info(f"  单票仓位 {position_pct*100:.0f}%, 最多持仓 {max_positions} 只, 目标总仓 {target_total_pos*100:.0f}%")

    for i, t_date in enumerate(trade_dates):
        # 5a. 大盘择时 + 周期判定
        regime = check_market_regime(index_df, t_date, breadth)

        # 5b. 检查已有持仓 → 触发止盈止损 → 卖出
        for code in list(positions.keys()):
            pos = positions[code]
            ind = indicators.get(code)
            if not ind:
                continue
            idx_t = ind["date_to_idx"].get(t_date)
            if idx_t is None or idx_t < 0 or idx_t >= len(ind["dates"]):
                continue

            sell_price = None
            sell_reason = None

            # 硬止损
            if ind["closes"][idx_t] < pos["stop_loss"]:
                sell_price = ind["closes"][idx_t]
                sell_reason = "硬止损"

            # 移动止盈（盈利 ≥ 6% 后从最高点回落 3%）
            if sell_price is None:
                # 从买入日到今天的最高
                buy_idx = pos["buy_idx"]
                high_since_buy = ind["highs"][buy_idx:idx_t+1].max()
                profit_pct = (high_since_buy / pos["buy_price"] - 1) * 100
                if profit_pct >= C.TRAILING_ACTIVATION_PCT:
                    drawdown = (high_since_buy - ind["closes"][idx_t]) / high_since_buy * 100
                    if drawdown >= C.TRAILING_PULLBACK_PCT:
                        sell_price = ind["closes"][idx_t]
                        sell_reason = "移动止盈"

            # MA5 止损
            if sell_price is None and idx_t >= pos["buy_idx"] + 5:
                closes_j = ind["closes"][pos["buy_idx"]:idx_t+1]
                if len(closes_j) >= 5:
                    ma5_j = closes_j[-5:].mean()
                    if ind["closes"][idx_t] < ma5_j:
                        sell_price = ind["closes"][idx_t]
                        sell_reason = "MA5止损"

            # 持仓到期
            if sell_price is None and (idx_t - pos["buy_idx"]) >= hold_days:
                sell_price = ind["closes"][idx_t]
                sell_reason = "到期"

            if sell_price is not None:
                # 计算收益（含手续费）— 单位 %
                ret_pct = (sell_price / pos["buy_price"] - 1) * 100 - ROUND_TRIP_COST * 100
                proceeds = pos["shares"] * sell_price * (1 - COMMISSION_PCT - STAMP_TAX_PCT - SLIPPAGE_PCT)
                cash += proceeds
                closed_trades.append({
                    "code": code,
                    "buy_date": pos["buy_date"],
                    "sell_date": t_date,
                    "buy_price": round(pos["buy_price"], 2),
                    "sell_price": round(sell_price, 2),
                    "ret_pct": round(ret_pct, 2),
                    "month": t_date[:7],
                    "sell_reason": sell_reason,
                })
                del positions[code]

        # 5c. 评估开仓信号（仅当 allow=True 且有空余仓位）
        current_pos = len(positions)
        available_slots = max_positions - current_pos
        if not regime["allow"]:
            available_slots = 0  # 退潮期不新开仓
        if regime.get("caution"):
            available_slots = min(available_slots, 2)  # 高潮期最多 2 只

        if available_slots > 0 and regime["allow"]:
            candidates = []
            for code, ind in indicators.items():
                if code in positions:
                    continue
                idx_t = ind["date_to_idx"].get(t_date)
                if idx_t is None or idx_t < 0:
                    continue

                close_t = ind["closes"][idx_t]
                ma5_t = ind["ma5"][idx_t]
                ma10_t = ind["ma10"][idx_t]
                ma20_t = ind["ma20"][idx_t]
                ma60_t = ind.get("ma60", [np.nan]*len(ind["closes"]))[idx_t] if use_ma60 else np.nan

                if np.isnan(ma5_t) or np.isnan(ma10_t) or np.isnan(ma20_t):
                    continue

                if use_ma60 and np.isnan(ma60_t):
                    continue

                # MA 多头
                if use_ma60:
                    if not (ma5_t > ma10_t > ma20_t > ma60_t):
                        continue
                else:
                    if not (ma5_t > ma10_t > ma20_t):
                        continue

                if close_t < ma5_t:
                    continue

                gain_20_t = ind["gain_20"][idx_t]
                if not np.isnan(gain_20_t) and gain_20_t >= C.PHASE_GAIN_MAX:
                    continue

                amount_t = ind["amounts"][idx_t]
                if amount_t > 0 and amount_t < C.MIN_TURNOVER_YUAN:
                    continue

                vol_5_t = ind["vol_5"][idx_t]
                vol_15_t = ind["vol_15"][idx_t]
                if not np.isnan(vol_5_t) and not np.isnan(vol_15_t):
                    if vol_5_t > vol_15_t * 3:
                        continue

                entry = close_t
                target = entry * (1 + target_pct)
                stop = entry * (1 - stop_pct)
                upside = target - entry
                downside = entry - stop
                if downside <= 0:
                    continue
                rr = upside / downside
                if rr < min_rr:
                    continue

                # 过滤：当日涨幅 0-10%（强势但不极端）
                chg = ind["closes"][idx_t] / ind["closes"][idx_t-1] - 1 if idx_t > 0 else 0
                if chg < -0.02 or chg > 0.10:
                    continue

                # 打分：RR + 涨幅 + MA 斜率
                score = rr * 10
                if 0.02 <= chg <= 0.06:
                    score += 15  # 温和上涨最佳
                if ma5_t > ma10_t > ma20_t:
                    score += 10

                candidates.append({
                    "code": code, "idx": idx_t, "entry": entry,
                    "rr": rr, "score": score, "chg": chg,
                })

            candidates.sort(key=lambda x: x["score"], reverse=True)
            picks = candidates[:available_slots]

            # 买入
            for pick in picks:
                if cash <= 0:
                    break
                code = pick["code"]
                next_idx = pick["idx"] + 1
                if next_idx >= len(indicators[code]["opens"]):
                    continue
                buy_price = indicators[code]["opens"][next_idx]
                if np.isnan(buy_price) or buy_price <= 0:
                    continue

                # 计算可买股数（按仓位比例 + 100 股整数）
                target_value = cash * position_pct
                shares = int(target_value / buy_price / 100) * 100  # 100 股一手
                if shares <= 0:
                    continue
                actual_cost = shares * buy_price * (1 + COMMISSION_PCT + SLIPPAGE_PCT)
                if actual_cost > cash:
                    shares = int(cash / (buy_price * (1 + COMMISSION_PCT + SLIPPAGE_PCT)) / 100) * 100
                    if shares <= 0:
                        continue
                    actual_cost = shares * buy_price * (1 + COMMISSION_PCT + SLIPPAGE_PCT)

                cash -= actual_cost
                positions[code] = {
                    "buy_price": buy_price,
                    "shares": shares,
                    "buy_date": t_date,
                    "buy_idx": pick["idx"] + 1,
                    "stop_loss": buy_price * (1 - stop_pct),
                    "target": buy_price * (1 + target_pct),
                }

        # 5d. 记录每日账户净值（持仓市值 + 现金）
        position_value = 0
        for code, pos in positions.items():
            ind = indicators.get(code)
            if not ind:
                continue
            idx_t = ind["date_to_idx"].get(t_date)
            if idx_t is None:
                idx_t = len(ind["dates"]) - 1
            position_value += pos["shares"] * ind["closes"][idx_t]
        nav = cash + position_value
        daily_log.append({
            "date": t_date,
            "nav": nav,
            "cash": cash,
            "position_value": position_value,
            "positions_count": len(positions),
            "phase": regime["phase"],
            "limit_up": regime.get("limit_up", 0),
        })

    # 6. 强制平仓所有持仓（按最后一日收盘）
    if positions:
        last_date = trade_dates[-1]
        for code in list(positions.keys()):
            pos = positions[code]
            ind = indicators.get(code)
            if not ind:
                continue
            idx_t = len(ind["dates"]) - 1
            sell_price = ind["closes"][idx_t]
            ret_pct = (sell_price / pos["buy_price"] - 1) * 100 - ROUND_TRIP_COST * 100
            proceeds = pos["shares"] * sell_price * (1 - COMMISSION_PCT - STAMP_TAX_PCT - SLIPPAGE_PCT)
            cash += proceeds
            closed_trades.append({
                "code": code, "buy_date": pos["buy_date"], "sell_date": last_date,
                "buy_price": round(pos["buy_price"], 2), "sell_price": round(sell_price, 2),
                "ret_pct": round(ret_pct, 2), "month": last_date[:7], "sell_reason": "回测结束",
            })
            del positions[code]

    # 7. 统计
    log.info(f"\n=== 回测完成 ===")
    log.info(f"总交易笔数: {len(closed_trades)}")
    log.info(f"最终现金: {cash:,.0f}")
    log.info(f"最终净值: {nav:,.0f}")

    return {
        "trades": closed_trades,
        "daily_log": daily_log,
        "initial_cash": initial_cash,
        "final_cash": cash,
        "final_nav": nav,
        "total_return_pct": round((nav / initial_cash - 1) * 100, 2),
        "round_trip_cost_pct": round(ROUND_TRIP_COST * 100, 3),
        "params": {
            "start": start, "end": end, "hold_days": hold_days, "top_n": top_n,
            "sample_n": sample_n, "max_positions": max_positions,
            "position_pct": position_pct, "target_pct": target_pct, "stop_pct": stop_pct,
            "min_rr": min_rr, "use_ma60": use_ma60,
        },
    }


def monthly_stats(daily_log: list[dict]) -> dict:
    """从 daily_log 统计月度收益"""
    if not daily_log:
        return {"months": [], "summary": {}}

    df = pd.DataFrame(daily_log)
    df["date"] = pd.to_datetime(df["date"])
    df["month"] = df["date"].dt.strftime("%Y-%m")
    df["daily_return"] = df["nav"].pct_change()
    df["daily_return"] = df["daily_return"].fillna(0)

    monthly = df.groupby("month").agg(
        start_nav=("nav", "first"),
        end_nav=("nav", "last"),
        avg_pos=("positions_count", "mean"),
        max_pos=("positions_count", "max"),
        phase_mode=("phase", lambda x: x.mode().iloc[0] if not x.mode().empty else "未知"),
        avg_limit_up=("limit_up", "mean"),
        trading_days=("nav", "count"),
    ).round(2)

    monthly["month_return"] = ((monthly["end_nav"] / monthly["start_nav"] - 1) * 100).round(2)
    monthly["cumulative_return"] = ((monthly["end_nav"] / monthly["start_nav"].iloc[0] - 1) * 100).round(2)

    win_months = sum(1 for r in monthly["month_return"] if r > 0)
    summary = {
        "total_months": len(monthly),
        "win_months": win_months,
        "win_month_rate": round(win_months / len(monthly) * 100, 2),
        "total_return_pct": round((monthly["end_nav"].iloc[-1] / monthly["start_nav"].iloc[0] - 1) * 100, 2),
        "avg_monthly_return_pct": round(monthly["month_return"].mean(), 2),
        "best_month_pct": round(monthly["month_return"].max(), 2),
        "worst_month_pct": round(monthly["month_return"].min(), 2),
    }
    return {"months": monthly.reset_index().to_dict("records"), "summary": summary}


def print_report(result: dict, monthly: dict):
    print(f"\n{'='*60}")
    print(f"📊 退学战法 v2 回测报告（方案 C：大盘择时 + 真实仓位 + 交易成本）")
    print(f"{'='*60}")
    p = result["params"]
    print(f"\n区间: {p['start']} → {p['end']}")
    print(f"持仓 {p['hold_days']} 天, top_n={p['top_n']}, 最大持仓 {p['max_positions']} 只")
    print(f"单票仓位 {p['position_pct']*100:.0f}%, 目标 {p['target_pct']*100:.0f}% / 止损 {p['stop_pct']*100:.0f}%")
    print(f"MA60: {'开启' if p['use_ma60'] else '关闭'}, 盈亏比下限: {p['min_rr']}")
    print(f"交易成本: {result['round_trip_cost_pct']}% (双边)")

    s = monthly["summary"]
    print(f"\n─── 总体表现 ───")
    print(f"  初始资金: {result['initial_cash']:,.0f}")
    print(f"  最终净值: {result['final_nav']:,.0f}")
    print(f"  累计收益: {s['total_return_pct']}%")
    print(f"  月均收益: {s['avg_monthly_return_pct']}%")
    print(f"  最佳月:   {s['best_month_pct']}%")
    print(f"  最差月:   {s['worst_month_pct']}%")
    print(f"  盈利月数: {s['win_months']}/{s['total_months']} ({s['win_month_rate']}%)")
    print(f"  总交易:   {len(result['trades'])} 笔")

    # 交易统计
    if result["trades"]:
        df = pd.DataFrame(result["trades"])
        wins = (df["ret_pct"] > 0).sum()
        print(f"  胜率:     {wins/len(df)*100:.1f}%")
        print(f"  平均收益: {df['ret_pct'].mean():.2f}%")
        print(f"  平均盈利: {df.loc[df['ret_pct']>0, 'ret_pct'].mean() if wins > 0 else 0:.2f}%")
        print(f"  平均亏损: {df.loc[df['ret_pct']<=0, 'ret_pct'].mean() if wins < len(df) else 0:.2f}%")
        print(f"  最大单笔: {df['ret_pct'].max():.2f}%")
        print(f"  最大亏损: {df['ret_pct'].min():.2f}%")

    print(f"\n─── 月度明细 ───")
    print(f"{'月份':<8} {'笔数':>4} {'持仓均':>6} {'周期':<8} {'涨停均':>6} {'月度收益':>8}")
    print("─" * 60)
    for m in monthly["months"]:
        # 找该月的交易数
        month_trades = [t for t in result["trades"] if t["month"] == m["month"]]
        print(f"{m['month']:<8} {len(month_trades):>4} {m['avg_pos']:>6.1f} "
              f"{m['phase_mode']:<8} {m['avg_limit_up']:>6.1f} {m['month_return']:>+8.2f}%")

    # 退潮空仓日
    log_df = pd.DataFrame(result["daily_log"])
    if "phase" in log_df.columns:
        phase_counts = log_df["phase"].value_counts()
        print(f"\n─── 周期分布 ───")
        for phase, cnt in phase_counts.items():
            print(f"  {phase}: {cnt} 天 ({cnt/len(log_df)*100:.1f}%)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="退学战法 v2 回测（大盘择时+真实仓位+交易成本）")
    parser.add_argument("--start", default="2025-07-01")
    parser.add_argument("--end", default="2026-06-30")
    parser.add_argument("--hold", type=int, default=7)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--sample", type=int, default=500)
    parser.add_argument("--max-pos", type=int, default=5, help="最多持仓数")
    parser.add_argument("--position", type=float, default=0.20, help="单票仓位")
    parser.add_argument("--target", type=float, default=0.10, help="止盈")
    parser.add_argument("--stop", type=float, default=0.05, help="止损")
    parser.add_argument("--min-rr", type=float, default=2.0)
    parser.add_argument("--ma60", action="store_true")
    parser.add_argument("--cash", type=float, default=100000)
    parser.add_argument("--output", default="reports/backtest_v2_report.json")
    args = parser.parse_args()

    result = backtest_v2(
        start=args.start, end=args.end,
        hold_days=args.hold, top_n=args.top,
        sample_n=args.sample, initial_cash=args.cash,
        max_positions=args.max_pos, position_pct=args.position,
        target_pct=args.target, stop_pct=args.stop,
        min_rr=args.min_rr, use_ma60=args.ma60,
    )
    monthly = monthly_stats(result["daily_log"])
    print_report(result, monthly)

    output = {"result": result, "monthly": monthly, "generated_at": datetime.now().isoformat()}
    output_path = ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # 去掉 daily_log 太长的字段
    output["result"]["daily_log"] = output["result"]["daily_log"][::5]  # 5 天采样一次
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    print(f"\n✅ 报告: {output_path}")