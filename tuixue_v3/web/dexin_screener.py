"""
web/dexin_screener.py — 得鑫量变术 · 时序链条量化选股引擎

时序链条 (K线结构优先, 量能为可选辅助):
  上升趋势(前置) → 藏诈诱多 → 虚杀洗盘 → 抛压清零(等待突破) → 得鑫主升

判定建立在日线 OHLCV 之上; modules(赛道/龙虎) 仅用于 de_xin 阶段门控。
"辩真"是贯穿全过程的方法论(纲领原话),不作为独立阶段。

对外仅暴露 register(app),挂载:
  GET /api/dexin/screen     — 时序链条分类(每阶段 ≤10, 带原话溯源 + phase_dates)
  GET /api/dexin/laws       — 纲领原话常量(供前端展示)
"""
from __future__ import annotations

import asyncio
import logging
import time as systime
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger("tuixue_v3.dexin_screener")


# ═══════════════════════════════════════════════════════════
#  纲领原话常量 — 判定与溯源的唯一基准
# ═══════════════════════════════════════════════════════════
_LAWS = {
    "creed": [
        "藏诈:涨跌皆为主笔,异动尽显迷局",
        "辩真:缩量暗藏筹策,放量方显玄机",
        "虚杀:主升常布虚杀,慧眼勘破幻虚",
        "得鑫:逆庄徒生缠斗,顺势直取千鑫",
    ],
    "stages": {
        "cang_zha": {
            "label": "藏诈·诱多洗筹",
            "creed_line": "藏诈:涨跌皆为主笔,异动尽显迷局",
            "supporting": "藏诈制造赚钱效应,勾起贪婪之心,引诱盲目追高",
            "advice": "【观望,禁止买入】",
            "op_kind": "watch_only",
        },
        "xu_sha": {
            "label": "虚杀·恐慌洗盘",
            "creed_line": "虚杀:主升常布虚杀,慧眼勘破幻虚",
            "supporting": "虚杀刻意砸盘震荡,制造极致恐慌,逼出手中筹码",
            "advice_benign": "【低吸观察仓,持续跟踪】",
            "advice_dangerous": "【直接剔除选股池,回避趋势走弱】",
            "op_kind": "paper_trade",
        },
        "clearing": {
            "label": "等待突破(抛压清零)",
            "creed_line": "辩真:缩量暗藏筹策,放量方显玄机",
            "supporting": "藏诈→虚杀后,股价跌不动、振幅收窄,卖盘耗尽筹码沉淀,等待放量突破藏诈高点",
            "advice": "【重点跟踪,等待得鑫放量信号】",
            "op_kind": "track",
        },
        "de_xin": {
            "label": "得鑫·主升浪启动",
            "creed_line": "得鑫:逆庄徒生缠斗,顺势直取千鑫",
            "supporting": "反复历经藏诈诱多、虚杀洗盘的洗牌过程后,盘面抛压彻底清零,真正稳健的主升行情就此开启,稳稳打通属于我们的得鑫通道",
            "advice": "【核心持仓,顺势持有,开启动态跟踪止盈】",
            "op_kind": "core_hold",
        },
    },
    "risk_control": "锐捷是板块情绪标的,周五炸板放量,若无法高举高打,回调一旦回补下方缺口,需及时离场。",
    "mindset": [
        "行情震荡、节奏混沌阶段,最适合沉淀认知打磨交易体系",
        "行情弱势守不住本心,行情火热抓不住机会,是大部分交易者的常态",
    ],
    "regime_chaos": "行情震荡、节奏混沌阶段,最适合沉淀认知打磨交易体系",
    "disclaimer": "本选股判定逻辑来源于公众号得鑫量变术主观交易体系,仅作为本地量化筛选参考,不构成任何投资建议,A股市场波动风险较高,请自主把控仓位。",
}


# ═══════════════════════════════════════════════════════════
#  纯函数: 箱体 / 缺口 检测
# ═══════════════════════════════════════════════════════════
def _detect_box(df: pd.DataFrame, lookback: int = 20) -> dict:
    """取近 lookback 日的高低区间, 排除极值噪音。"""
    if df is None or len(df) < 5:
        return {"low": 0.0, "high": 0.0, "width_pct": 0.0, "pos": 0.5}
    win = df.tail(lookback)
    low = float(win["最低"].min())
    high = float(win["最高"].max())
    close = float(df["收盘"].iloc[-1])
    width_pct = (high - low) / low * 100 if low > 0 else 0.0
    if high > low:
        pos = (close - low) / (high - low)
        pos = max(0.0, min(1.0, pos))
    else:
        pos = 0.5
    return {"low": round(low, 2), "high": round(high, 2), "width_pct": round(width_pct, 2), "pos": round(pos, 2)}


def _detect_gaps(df: pd.DataFrame, lookback: int = 30, min_gap_pct: float = 2.0) -> list[dict]:
    """找出近 lookback 日内所有未回补的跳空缺口(上跳/下跳)。min_gap_pct 默认 2% (过滤噪声)。"""
    if df is None or len(df) < 3:
        return []
    win = df.tail(lookback).reset_index(drop=True)
    gaps: list[dict] = []
    for i in range(1, len(win)):
        prev_h = float(win["最高"].iloc[i - 1])
        prev_l = float(win["最低"].iloc[i - 1])
        cur_h = float(win["最高"].iloc[i])
        cur_l = float(win["最低"].iloc[i])
        ref = max(prev_h, prev_l, 1e-9)
        if cur_l > prev_h:                              # 向上跳空
            gap_pct = (cur_l - prev_h) / ref * 100
            if gap_pct >= min_gap_pct:
                gaps.append({"idx": i, "type": "up", "low": prev_h, "high": cur_l,
                             "size_pct": round(gap_pct, 2),
                             "date": str(win["日期"].iloc[i])[:10]})
        elif cur_h < prev_l:                            # 向下跳空
            gap_pct = (prev_l - cur_h) / ref * 100
            if gap_pct >= min_gap_pct:
                gaps.append({"idx": i, "type": "down", "low": cur_h, "high": prev_l,
                             "size_pct": round(gap_pct, 2),
                             "date": str(win["日期"].iloc[i])[:10]})
    return gaps


def _gap_filled(df: pd.DataFrame, gap: dict) -> bool:
    """判断缺口是否在后续被回补(向上跳空: 后续最低 ≤ 缺口下沿; 向下跳空: 后续最高 ≥ 缺口上沿)。"""
    if df is None or len(df) < 2 or not gap:
        return False
    win = df.reset_index(drop=True)
    gap_idx = gap["idx"]
    if gap["type"] == "up":
        # 向上跳空: 缺口区是 (gap.low, gap.high); 后续最低 < gap.low 即回补
        after = win["最低"].iloc[gap_idx + 1:]
        return bool((after < gap["low"]).any()) if len(after) else False
    else:  # down
        after = win["最高"].iloc[gap_idx + 1:]
        return bool((after > gap["high"]).any()) if len(after) else False


def _gap_status(df: pd.DataFrame, lookback: int = 30) -> dict:
    """聚合: 是否有未回补的上跳/下跳缺口 + 哪些已被回补。"""
    gaps = _detect_gaps(df, lookback=lookback)
    filled = []
    unfilled = []
    for g in gaps:
        if _gap_filled(df, g):
            filled.append(g)
        else:
            unfilled.append(g)
    return {
        "has_unfilled_up": any(g["type"] == "up" for g in unfilled),
        "has_unfilled_down": any(g["type"] == "down" for g in unfilled),
        "unfilled_count": len(unfilled),
        "filled_count": len(filled),
        "filled_gaps": [{"type": g["type"], "date": g["date"]} for g in filled],
    }


# ═══════════════════════════════════════════════════════════
#  纯函数: 时序链条判定 (DexinTrendAgent)
#  阶段: no_uptrend → none → cang_zha → xu_sha → clearing → de_xin
#  K线结构优先, 量能为可选辅助 (USE_VOLUME_CONFIRM 开关)。
# ═══════════════════════════════════════════════════════════
def _amplitude_series(df: pd.DataFrame) -> pd.Series:
    """振幅% (单日 high-low 相对昨收)。"""
    if df is None or len(df) < 2:
        return pd.Series(dtype=float)
    prev_close = df["收盘"].shift(1)
    amp = (df["最高"] - df["最低"]) / prev_close.replace(0, np.nan) * 100
    return amp.fillna(0.0)


def _date_label(val) -> str:
    """把日期值安全转 'MM-DD' 字符串 (兼容 datetime / Timestamp / str)."""
    if val is None:
        return ""
    try:
        s = str(val)[:10]
        # ISO YYYY-MM-DD → MM-DD
        if len(s) == 10 and s[4] == "-":
            return s[5:]
        return s
    except Exception:
        return ""


def _date_range_label(start_val, end_val) -> str:
    """'MM-DD 至 MM-DD'."""
    s = _date_label(start_val)
    e = _date_label(end_val)
    if not s or not e:
        return ""
    return f"{s} 至 {e}"


class DexinTrendAgent:
    """得鑫量变术 · 时序链条判定 Agent.

    严格按原文语义: 上升趋势(前置) → 藏诈诱多 → 虚杀洗盘 → 抛压清零 → 得鑫突破.
    K线结构优先, 量能仅作辅助验证项 (USE_VOLUME_CONFIRM=False 可完全关闭).
    """

    WINDOW_DAYS = 20
    TREND_MA_PERIOD = 20
    TREND_MA_SLOPE_DAYS = 5
    TREND_MIN_RISE = 0.0
    FRAUD_MIN_RISE = 0.03
    FRAUD_NEW_HIGH_PERIOD = 10
    FRAUD_BODY_RATIO = 0.7
    KILL_MIN_FALL = 0.03
    KILL_AFTER_FRAUD_MAX = 5
    KILL_MAX_DRAWDOWN = 0.30        # 回撤超过 30% 直接当作崩盘拒接, 不再视为洗盘
    KILL_DRAWDOWN_DANGER = 0.15     # 回撤超过 15% 仍算洗盘, 但 variant=dangerous
    CLEAR_LOW_DAYS = 3
    CLEAR_AMPLITUDE_SHRINK = 0.6
    GAIN_MIN_RISE = 0.04
    GAIN_BREAK_WINDOW = 10
    USE_VOLUME_CONFIRM = True
    GAIN_VOL_RATIO = 1.2

    def __init__(self, params: dict | None = None):
        if params:
            for k, v in params.items():
                if hasattr(self, k):
                    setattr(self, k, v)

    # ── 数据预处理 ──
    def _preprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy().reset_index(drop=True)
        df["_close"] = df["收盘"].astype(float)
        df["_open"]  = df["开盘"].astype(float)
        df["_high"]  = df["最高"].astype(float)
        df["_low"]   = df["最低"].astype(float)
        df["_vol"]   = df["成交量"].astype(float)
        df["_date"]  = df["日期"]
        df["_ma20"]  = df["_close"].rolling(self.TREND_MA_PERIOD).mean()
        df["_vol_ma5"] = df["_vol"].rolling(5).mean()
        df["_chg"]   = df["_close"].pct_change()                  # 日涨幅(小数)
        df["_body"]  = (df["_close"] - df["_open"]).abs()         # K线实体(绝对)
        df["_range"] = (df["_high"] - df["_low"]).replace(0, np.nan)  # 影线总长
        df["_is_red"] = df["_close"] > df["_open"]                # 阳线
        df["_close_high_n"] = df["_close"].rolling(self.FRAUD_NEW_HIGH_PERIOD).max()
        df["_amp"]   = (df["_high"] - df["_low"]) / df["_close"].shift(1).replace(0, np.nan)
        df["_rise_20d"] = df["_close"].pct_change(self.WINDOW_DAYS)
        df["_ma20_slope"] = df["_ma20"].pct_change(self.TREND_MA_SLOPE_DAYS)
        return df

    # ── Step 1: 趋势前置过滤 ──
    def _check_uptrend(self, df: pd.DataFrame, end_idx: int) -> bool:
        if end_idx < self.WINDOW_DAYS:
            return False
        close = df["_close"].iloc[end_idx]
        ma20 = df["_ma20"].iloc[end_idx]
        slope = df["_ma20_slope"].iloc[end_idx]
        rise = df["_rise_20d"].iloc[end_idx]
        if pd.isna(ma20) or pd.isna(slope) or pd.isna(rise):
            return False
        price_above_ma = close > ma20
        ma_up = slope > 0
        total_rise = rise >= self.TREND_MIN_RISE
        return bool(price_above_ma and ma_up and total_rise)

    # ── Step 2: 藏诈识别 ──
    def _find_fraud_days(self, df: pd.DataFrame, start_idx: int, end_idx: int) -> list[int]:
        if end_idx < start_idx:
            return []
        sub = df.iloc[start_idx:end_idx + 1]
        # 高点 + 涨幅达标 + 阳线 + 实体占比 ≥ FRAUD_BODY_RATIO
        prev_high = sub["_close_high_n"].shift(1)
        body_ratio = sub["_body"] / sub["_range"]
        cond = (
            (sub["_chg"] >= self.FRAUD_MIN_RISE) &
            (sub["_close"] >= prev_high) &
            (sub["_is_red"] == True) &
            (body_ratio >= self.FRAUD_BODY_RATIO)
        )
        return [i for i, ok in cond.items() if ok]

    # ── Step 3: 虚杀匹配 ──
    def _match_virtual_kill(self, df: pd.DataFrame, fraud_idx: int) -> tuple[bool, int | None, tuple[int, int] | None, float, bool]:
        """返回 (matched, kill_idx, (kill_start, kill_end), drawdown, dangerous_break).

        dangerous_break = True 表示虚杀日收盘价跌破 ma20 (趋势走弱).
        外层据此把 variant 标 dangerous, 仍计入 xu_sha 桶 (不静默丢弃).
        """
        start = fraud_idx + 1
        end = min(fraud_idx + self.KILL_AFTER_FRAUD_MAX, len(df) - 1)
        if start >= len(df) or end - start < 1:
            return False, None, None, 0.0, False

        fraud_high = float(df["_high"].iloc[fraud_idx])

        # 寻找跌幅 ≥ KILL_MIN_FALL 的阴线
        sub = df.iloc[start:end + 1]
        kill_candidates = sub[(sub["_chg"] <= -self.KILL_MIN_FALL) & (sub["_is_red"] == False)]
        if kill_candidates.empty:
            return False, None, None, 0.0, False

        kill_idx = int(kill_candidates.index[0])
        kill_low = float(df["_low"].iloc[kill_idx])

        # 回撤幅度约束
        drawdown = (fraud_high - kill_low) / fraud_high if fraud_high > 0 else 0.0
        if drawdown > self.KILL_MAX_DRAWDOWN:
            return False, None, None, float(drawdown), False

        # 趋势走弱信号: 虚杀日收盘 < ma20 (持仓者已破位, 主力出货)
        dangerous_break = False
        ma20_at_kill = df["_ma20"].iloc[kill_idx]
        if not pd.isna(ma20_at_kill):
            close_at_kill = float(df["_close"].iloc[kill_idx])
            if close_at_kill < ma20_at_kill:
                dangerous_break = True

        kill_phase = (start, kill_idx)
        return True, kill_idx, kill_phase, float(drawdown), dangerous_break

    # ── Step 4: 抛压清零 ──
    def _check_pressure_clear(self, df: pd.DataFrame, kill_start: int, kill_end: int) -> tuple[bool, int | None]:
        start = kill_end + 1
        end = min(kill_end + self.CLEAR_LOW_DAYS + 2, len(df) - 1)
        if start >= len(df) or end - start < self.CLEAR_LOW_DAYS - 1:
            return False, None

        sub = df.iloc[start:end + 1]
        kill_low = float(df["_low"].iloc[kill_end])

        # 1) 连续不创新低
        if not (sub["_low"] >= kill_low).all():
            return False, None

        # 2) 振幅收缩: 末端日均振幅 ≤ 藏诈阶段振幅的 60%
        #    藏诈阶段振幅 = 虚杀前一日 (kill_start - 1) 的振幅
        fraud_amp_idx = kill_start - 1
        if fraud_amp_idx < 0:
            return False, None
        fraud_amp = float(df["_amp"].iloc[fraud_amp_idx])
        if pd.isna(fraud_amp) or fraud_amp <= 0:
            return False, None
        avg_clear_amp = float(sub["_amp"].mean())
        if pd.isna(avg_clear_amp) or avg_clear_amp > fraud_amp * self.CLEAR_AMPLITUDE_SHRINK:
            return False, None

        return True, int(end)

    # ── Step 5: 得鑫突破 ──
    def _check_gain_breakout(self, df: pd.DataFrame, kill_end_idx: int, fraud_high: float) -> tuple[bool, int | None]:
        # 突破窗口: 必须发生在虚杀结束日 KILL_AFTER_FRAUD_MAX 天之后, 至 df 末尾
        start = max(0, kill_end_idx + 1)
        end = min(kill_end_idx + self.GAIN_BREAK_WINDOW, len(df) - 1)
        if end < start:
            return False, None

        sub = df.iloc[start:end + 1]
        cond = (
            (sub["_close"] >= fraud_high) &
            (sub["_chg"] >= self.GAIN_MIN_RISE) &
            (sub["_is_red"] == True)
        )
        if self.USE_VOLUME_CONFIRM:
            vol_cond = sub["_vol"] >= sub["_vol_ma5"] * self.GAIN_VOL_RATIO
            cond = cond & vol_cond

        gain_idx_list = [i for i, ok in cond.items() if ok]
        if not gain_idx_list:
            return False, None
        return True, gain_idx_list[0]

    # ── 主入口 ──
    def detect(self, df: pd.DataFrame, modules: dict | None = None) -> dict:
        if df is None or len(df) < self.WINDOW_DAYS + 5:
            return _empty_result("数据不足(需 ≥ 25 个交易日)")

        df = self._preprocess(df)
        total = len(df)
        window_end = total - 1
        window_start = window_end - self.WINDOW_DAYS + 1

        modules = modules or {}

        # ── Step 1: 上升趋势前置 ──
        if not self._check_uptrend(df, window_end):
            return _stage_result(
                phase="no_uptrend", stage="none",
                label="非上升趋势", variant=None,
                advice="【观望,非上升趋势,继续跟踪】",
                signals={"reason": "收盘 ≤ MA20 或 MA20 斜率 ≤ 0 或近 20 日累计涨幅 < 0"},
                phase_dates={},
            )

        # ── Step 2: 找藏诈 ──
        fraud_days = self._find_fraud_days(df, window_start, window_end)
        if not fraud_days:
            return _stage_result(
                phase="none", stage="none",
                label="暂无形态", variant=None,
                advice="【观望,日线整体向上但无藏诈异动】",
                signals={"fraud_count_20d": 0},
                phase_dates={},
            )

        # ── Step 3-5: 遍历藏诈 → 匹配虚杀 → 验证清零 → 检测突破 ──
        # 记录最近一次"走完整链条"的进展, 用于显示"洗盘进行时"等中间态
        last_progress: dict = {}
        last_fraud_idx = fraud_days[-1]

        for fraud_idx in fraud_days:
            matched, kill_idx, kill_phase, drawdown, dangerous_break = self._match_virtual_kill(df, fraud_idx)
            if not matched:
                # 仅记录"该藏诈无匹配虚杀"的进展
                last_progress = {
                    "stage": "cang_zha", "variant": None,
                    "phase_dates": {"藏诈日": _date_label(df["_date"].iloc[fraud_idx])},
                }
                continue

            kill_start, kill_end = kill_phase
            fraud_date = _date_label(df["_date"].iloc[fraud_idx])
            kill_date  = _date_label(df["_date"].iloc[kill_idx])

            # 危险剔除: 虚杀回撤 > 危险阈值 OR 虚杀日收盘跌破 ma20 → 走弱, dangerous
            if drawdown > self.KILL_DRAWDOWN_DANGER or dangerous_break:
                last_progress = {
                    "stage": "xu_sha", "variant": "dangerous",
                    "phase_dates": {
                        "藏诈日": fraud_date,
                        "虚杀日": kill_date,
                    },
                    "drawdown_pct": round(drawdown * 100, 2),
                    "dangerous_reason": "drawdown>15%" if drawdown > self.KILL_DRAWDOWN_DANGER else "close<ma20",
                }
                continue

            # 验证抛压清零
            clear_ok, clear_end = self._check_pressure_clear(df, kill_start, kill_end)
            fraud_high = float(df["_high"].iloc[fraud_idx])

            if not clear_ok:
                last_progress = {
                    "stage": "xu_sha", "variant": "benign",
                    "phase_dates": {
                        "藏诈日": fraud_date,
                        "虚杀日": kill_date,
                        "洗盘区间": _date_range_label(
                            df["_date"].iloc[max(0, kill_start - 1)],
                            df["_date"].iloc[kill_end],
                        ),
                    },
                    "drawdown_pct": round(drawdown * 100, 2),
                }
                continue

            # 抛压清零 → 检查得鑫突破
            gain_ok, gain_idx = self._check_gain_breakout(df, kill_end, fraud_high)
            if gain_ok:
                return _stage_result(
                    phase="de_xin", stage="de_xin",
                    label=_LAWS["stages"]["de_xin"]["label"],
                    variant=None,
                    advice=_LAWS["stages"]["de_xin"]["advice"],
                    signals={
                        "fraud_high": round(fraud_high, 2),
                        "breakout_chg_pct": round(float(df["_chg"].iloc[gain_idx]) * 100, 2),
                        "drawdown_pct": round(drawdown * 100, 2),
                    },
                    phase_dates={
                        "藏诈日": fraud_date,
                        "虚杀日": kill_date,
                        "洗盘区间": _date_range_label(
                            df["_date"].iloc[max(0, kill_start - 1)],
                            df["_date"].iloc[clear_end],
                        ),
                        "得鑫日": _date_label(df["_date"].iloc[gain_idx]),
                        "cycle_days": int(gain_idx - fraud_idx),
                    },
                )

            # 抛压清零但未突破 → 等待突破
            last_progress = {
                "stage": "clearing", "variant": None,
                "phase_dates": {
                    "藏诈日": fraud_date,
                    "虚杀日": kill_date,
                    "洗盘区间": _date_range_label(
                        df["_date"].iloc[max(0, kill_start - 1)],
                        df["_date"].iloc[clear_end],
                    ),
                },
                "drawdown_pct": round(drawdown * 100, 2),
            }

        # 未触发得鑫 → 返回最后一次进展
        if not last_progress:
            return _stage_result(
                phase="cang_zha", stage="cang_zha",
                label=_LAWS["stages"]["cang_zha"]["label"],
                variant=None,
                advice=_LAWS["stages"]["cang_zha"]["advice"],
                signals={"fraud_count_20d": len(fraud_days)},
                phase_dates={"藏诈日": _date_label(df["_date"].iloc[last_fraud_idx])},
            )

        stage_key = last_progress["stage"]
        variant = last_progress["variant"]
        stage_law = _LAWS["stages"].get(stage_key, {})
        if variant == "dangerous":
            advice = _LAWS["stages"]["xu_sha"]["advice_dangerous"]
        elif variant == "benign":
            advice = _LAWS["stages"]["xu_sha"]["advice_benign"]
        else:
            advice = stage_law.get("advice", "—")

        return _stage_result(
            phase=stage_key, stage=stage_key,
            label=stage_law.get("label", stage_key),
            variant=variant,
            advice=advice,
            signals={"fraud_count_20d": len(fraud_days),
                     "drawdown_pct": last_progress.get("drawdown_pct", 0)},
            phase_dates=last_progress["phase_dates"],
        )


def _empty_result(msg: str) -> dict:
    return {
        "stage": "none", "stage_label": "数据不足", "variant": None,
        "phase": "none",
        "quote": "", "advice": msg, "op_kind": "none",
        "phase_dates": {}, "cycle_days": 0,
        "signals": {},
    }


def _stage_result(phase: str, stage: str, label: str, variant: str | None,
                  advice: str, signals: dict, phase_dates: dict) -> dict:
    stage_law = _LAWS["stages"].get(stage, {})
    creed_line = stage_law.get("creed_line", "")
    supporting = stage_law.get("supporting", "")
    op_kind = stage_law.get("op_kind", "none")
    quote = f"{creed_line} — {supporting}" if creed_line else ""
    return {
        "stage": stage, "stage_label": label, "variant": variant,
        "phase": phase,
        "quote": quote, "advice": advice, "op_kind": op_kind,
        "phase_dates": phase_dates, "cycle_days": phase_dates.get("cycle_days", 0),
        "signals": signals,
    }


# 向后兼容: 旧版 API 仍可调用, 内部委托给 DexinTrendAgent
def _classify_stage(df: pd.DataFrame, modules: dict | None = None) -> dict:
    """兼容旧版 _classify_stage 调用, 返回字段保持 stage/stage_label/variant/quote/advice/signals.

    阶段映射: de_xin / clearing / xu_sha / cang_zha / none / no_uptrend.
    """
    if df is None or len(df) < 12:
        return {"stage": "none", "stage_label": "数据不足", "variant": None,
                "quote": "", "advice": "", "signals": {},
                "phase": "none", "phase_dates": {}, "cycle_days": 0}
    return DexinTrendAgent().detect(df, modules=modules)


# ═══════════════════════════════════════════════════════════
#  候选池构建
# ═══════════════════════════════════════════════════════════
async def _build_candidate_pool() -> tuple[list[str], dict[str, dict]]:
    """去重取 ≤80 候选: 热门赛道 + 当日活跃 + 近期涨停梯队。控制在 25s 框架内能完成日线批量。"""
    import time as _t
    from .. import multi_source_fetchers as msf
    _t_pool0 = _t.monotonic()

    pool: dict[str, dict] = {}

    # 1) 全市场活跃股(换手≥2% 或 振幅≥4% 或 涨幅≥5%)  → 直接拿活跃主力
    _ta = _t.monotonic()
    try:
        spot = await asyncio.wait_for(
            asyncio.to_thread(msf.fetch_spot_a_full, 6),
            timeout=10.0,
        )
    except (asyncio.TimeoutError, Exception) as e:
        log.warning(f"dexin fetch_spot_a_full 失败: {type(e).__name__}: {e}")
        spot = {}
    _dt_spot = _t.monotonic() - _ta

    for code, info in (spot or {}).items():
        try:
            chg = float(info.get("涨跌幅", 0) or 0)
            turn = float(info.get("换手率", 0) or 0)
            amp = float(info.get("振幅", 0) or 0)
        except Exception:
            continue
        if chg >= 5.0 or turn >= 3.0 or amp >= 5.0:
            pool[code] = info

    # ── 优化: 如果 spot 已 ≥80 只, 直接用 spot, 跳过 hot/zt 的额外 5s ──
    if len(pool) >= 80:
        ranked = sorted(pool.items(), key=lambda kv: -float((kv[1] or {}).get("成交额", 0) or 0))[:80]
        pool = {k: v for k, v in ranked}
        log.info(f"dexin pool profile: total={_t.monotonic()-_t_pool0:.2f}s spot={_dt_spot:.2f}s({len(spot or {})}codes) skip-others final={len(pool)}")
        return list(pool.keys()), pool

    # 2+3) 热门赛道 + 近期涨停梯队 并行拉取 (都只是往 spot 已有 code 上做富集, 依赖步骤 1 的 spot)
    from . import all_stocks

    async def _hot():
        try:
            return await asyncio.wait_for(asyncio.to_thread(msf.fetch_hot_sectors, 5, 5), timeout=6.0)
        except (asyncio.TimeoutError, Exception) as e:
            log.warning(f"dexin fetch_hot_sectors 失败: {type(e).__name__}: {e}")
            return []

    async def _rzt():
        try:
            return await asyncio.wait_for(asyncio.to_thread(all_stocks._fetch_recent_zt), timeout=4.0)
        except (asyncio.TimeoutError, Exception) as e:
            log.warning(f"dexin _fetch_recent_zt 失败: {type(e).__name__}: {e}")
            return {}

    _tb = _t.monotonic()
    hot_secs, recent_zt = await asyncio.gather(_hot(), _rzt())
    _dt_others = _t.monotonic() - _tb

    for sec in (hot_secs or []):
        codes = sec.get("codes") or []
        for c in codes[:5]:
            if c not in pool and spot and c in spot:
                pool[c] = spot[c]

    for code, info in (recent_zt or {}).items():
        if spot and code in spot:
            pool[code] = {**spot[code], **info}

    # ── 截 top 80 by 成交额 ──
    ranked = sorted(pool.items(), key=lambda kv: -float((kv[1] or {}).get("成交额", 0) or 0))[:80]
    pool = {k: v for k, v in ranked}
    log.info(f"dexin pool profile: total={_t.monotonic()-_t_pool0:.2f}s spot={_dt_spot:.2f}s({len(spot or {})}codes) others={_dt_others:.2f}s final={len(pool)}")
    return list(pool.keys()), pool


# ═══════════════════════════════════════════════════════════
#  单只股票模块数据 enrichment
# ═══════════════════════════════════════════════════════════
def _enrich_modules(code: str, info: dict | None, dailies: dict) -> dict:
    """组装 _classify_stage 需要的 modules: 赛道/量能/龙虎

    实时主力净流入单独走 fund_flow(per-stock 限频), 在 _do_screen 顶层一次性并发拉取并缓存.
    此函数只做 spot-derived 信号 + 用 cache 中的 dragon_net_yi.
    """
    info = info or {}
    modules: dict = {}
    # 量能(实时快照)
    modules["amount_yi"] = round(float(info.get("成交额", 0) or 0) / 1e8, 2)
    modules["turnover_pct"] = round(float(info.get("换手率", 0) or 0), 2)
    modules["amplitude"] = round(float(info.get("振幅", 0) or 0), 2)
    modules["vol_ratio"] = round(float(info.get("量比", 0) or 0), 2)
    modules["change_pct"] = round(float(info.get("涨跌幅", 0) or 0), 2)
    # 板块热度(从 spot 推断: 有流通市值+换手即可)
    modules["sector_strong"] = modules["amount_yi"] > 1.0 and modules["turnover_pct"] >= 2.0
    # 龙虎(来自顶层批量缓存, 不在本函数内发起 HTTP)
    modules["dragon_net_yi"] = float(info.get("_dragon_net_yi", 0) or 0)
    return modules


def _enrich_dragon_detail(code: str, cached_dragon: dict | None = None) -> dict:
    """龙虎/游资/机构 详情. 优先用批量预取缓存; 无缓存时返空(避免 per-code HTTP 雪崩)."""
    if cached_dragon:
        return cached_dragon
    return {"net_yi": 0.0, "seat_summary": "—", "risk_flag": None, "rows": []}


def _enrich_sector(code: str) -> dict:
    try:
        from . import sector_classify
        sec = sector_classify.get_sector(code) or {}
        tax = sec.get("taxonomy", {}) or {}
        return {
            "l1": tax.get("level1_cluster") or "—",
            "l2": tax.get("level2_sw") or "—",
            "l3": tax.get("level3_chain") or "—",
            "l4": tax.get("level4_subconcept") or "—",
            "role": tax.get("role") or "—",
        }
    except Exception:
        return {"l1": "—", "l2": "—", "l3": "—", "l4": "—", "role": "—"}


# ═══════════════════════════════════════════════════════════
#  端点注册
# ═══════════════════════════════════════════════════════════
def register(app):
    """挂载得鑫量变术路由到 FastAPI app。"""
    _screen_cache: dict = {"data": None, "ts": 0.0}
    _SCREEN_TTL = 300  # 5min
    _screen_lock = asyncio.Lock()  # 单飞: 冷缓存时防止并发重复跑 _do_screen

    async def _do_screen() -> dict:
        import time as _t
        from .server import envelope
        from .. import data_layer
        _t0 = _t.monotonic()

        # 1) 候选池
        _t1 = _t.monotonic()
        codes, spot_map = await _build_candidate_pool()
        _dt_pool = _t.monotonic() - _t1

        universe_total = len(spot_map)

        # 2) 批量日线(60d) — batch_fetch_daily 走 Redis + SQLite 缓存, 通常秒级。
        #    冷启 / SQLite WAL 争抢时可能超过 15s → 给 22s 预算 (仍 < 25s 中间件上限)。
        #    to_thread 不可真正取消, 超时后线程仍在跑并落 SQLite 缓存, 下次 5min 内直接命中。
        _t2 = _t.monotonic()
        try:
            dailies = await asyncio.wait_for(
                asyncio.to_thread(data_layer.batch_fetch_daily, codes, 60, 50),
                timeout=22.0,
            )
        except (asyncio.TimeoutError, Exception) as e:
            log.warning(f"dexin batch_fetch_daily 失败: {type(e).__name__}: {e}")
            dailies = {}
        _dt_fetch = _t.monotonic() - _t2

        # 3) 资金流(per-code 限频 + 雪崩, 跳过批量拉取, 用 spot-derived 代理)
        #    Eastmoney push2his 当前 push2 接口基本全挂 (per memory feedback_eastmoney_weekend_outage).
        #    不发起 per-code HTTP, 用 spot 已有字段近似; dragon_net_yi 默认 0, de_xin 改由 sector_strong 单门控.
        fund_cache: dict[str, float] = {}

        # 4) 逐只分类
        # 新版桶: cang_zha / xu_sha / clearing / de_xin (+ xu_sha_dangerous 风险池)
        bucketed: dict[str, list[dict]] = {k: [] for k in ("cang_zha", "xu_sha", "clearing", "de_xin")}
        xu_sha_d_raw: list[dict] = []

        for code in codes:
            df = dailies.get(code)
            if df is None or len(df) < 12:
                continue
            info = spot_map.get(code, {})
            modules = _enrich_modules(code, info, dailies)
            cls = _classify_stage(df, modules=modules)
            stage = cls["stage"]
            if stage not in bucketed:
                continue

            # de_xin 模块门控: 赛道 or 主力净流入 至少一边支持(震荡期可放宽)
            # 当前 push2 接口基本全挂, dragon_net_yi 默认 0, 主要靠 sector_strong
            if stage == "de_xin":
                if not (modules.get("sector_strong") or float(modules.get("dragon_net_yi", 0) or 0) > 0):
                    # 不满足模块门控 → 降级到 clearing (等突破但当前赛道资金不支持)
                    cls = dict(cls)
                    cls["stage"] = "clearing"
                    cls["stage_label"] = _LAWS["stages"]["clearing"]["label"]
                    cls["quote"] = f"{_LAWS['stages']['clearing']['creed_line']} — {_LAWS['stages']['clearing']['supporting']}"
                    cls["advice"] = _LAWS["stages"]["clearing"]["advice"]
                    cls["op_kind"] = _LAWS["stages"]["clearing"]["op_kind"]
                    stage = "clearing"

            dragon = _enrich_dragon_detail(code, cached_dragon={
                "net_yi": float(fund_cache.get(code, 0.0) or 0.0),
                "seat_summary": "主力净流入代理(成交额+量比)" if fund_cache.get(code) else "—",
                "risk_flag": None,
                "rows": [],
            })
            sector = _enrich_sector(code)
            box = _detect_box(df, lookback=20)
            gap_info = _gap_status(df, lookback=30)

            item = {
                "code": code,
                "name": info.get("name", "") or code,
                "stage": stage,
                "stage_label": cls["stage_label"],
                "variant": cls.get("variant"),
                "quote": cls.get("quote", ""),
                "advice": cls.get("advice", ""),
                "op_kind": cls.get("op_kind", "none"),
                "phase_dates": cls.get("phase_dates", {}),
                "cycle_days": cls.get("cycle_days", 0),
                "sector": sector,
                "volume": {
                    "amount_yi": modules.get("amount_yi", 0.0),
                    "vol_ratio": modules.get("vol_ratio", 0.0),
                    "turnover_pct": modules.get("turnover_pct", 0.0),
                    "amplitude": modules.get("amplitude", 0.0),
                    "change_pct": modules.get("change_pct", 0.0),
                },
                "box": box,
                "gap": gap_info,
                "dragon": dragon,
                "signals": cls.get("signals", {}),
            }
            # 危险虚杀 → 单独记, 不进 xu_sha 桶
            if stage == "xu_sha" and cls.get("variant") == "dangerous":
                xu_sha_d_raw.append(item)
                continue
            bucketed[stage].append(item)

        # 4) 排序 + 截 top 10
        def _rank(item, prefer_high=True):
            # 综合: 量能 + 涨幅 + 箱体位置(得鑫希望高) + 龙虎净
            v = item.get("volume", {})
            d = item.get("dragon", {})
            s = v.get("change_pct", 0) * 0.4 + (v.get("amount_yi", 0) > 0 and min(v["amount_yi"], 5) * 4) \
                + (item.get("box", {}).get("pos", 0.5) * 10 if prefer_high else 0) \
                + d.get("net_yi", 0) * 0.5
            return -s

        # 危险虚杀: 不进 top10(只是风险池), 用 _dangerous 子列表返回(不计入主桶)
        bucketed["xu_sha_dangerous"] = sorted(xu_sha_d_raw, key=lambda x: -x.get("dragon", {}).get("net_yi", 0))[:10]

        for k, lst in bucketed.items():
            if k == "xu_sha_dangerous":
                continue
            bucketed[k] = sorted(lst, key=lambda x: _rank(x, prefer_high=(k == "de_xin")))[:10]

        # 5) 行情 regime 判定(震荡/主升)
        total = sum(len(bucketed[k]) for k in ("cang_zha", "xu_sha", "clearing", "de_xin"))
        de_xin_ratio = (len(bucketed["de_xin"]) / total) if total > 0 else 0.0
        if de_xin_ratio > 0.18:
            regime = "主升期"
            regime_quote = "得鑫:逆庄徒生缠斗,顺势直取千鑫"
        elif de_xin_ratio > 0.06:
            regime = "结构性主升"
            regime_quote = "辩真:缩量暗藏筹策,放量方显玄机"
        else:
            regime = "震荡混沌期"
            regime_quote = _LAWS["regime_chaos"]

        # 6) 附加每只股票尾部统一风险提示(原文要求)
        for k in bucketed:
            for stk in bucketed[k]:
                stk["disclaimer"] = _LAWS["disclaimer"]

        _dt_total = _t.monotonic() - _t0
        log.info(
            f"dexin profile: total={_dt_total:.2f}s pool={_dt_pool:.2f}s({len(codes)}codes) "
            f"fetch={_dt_fetch:.2f}s({len(dailies)}dailies) classify={_dt_total - _dt_pool - _dt_fetch:.2f}s"
        )

        return {
            "stages": bucketed,
            "universe_total": universe_total,
            "candidate_total": len(codes),
            "classified_total": total,
            "regime": regime,
            "regime_quote": regime_quote,
            "laws": _LAWS,
            "disclaimer": _LAWS["disclaimer"],
            "ts": datetime.now().isoformat(),
        }

    @app.get("/api/dexin/screen")
    async def api_dexin_screen(refresh: bool = False):
        from .server import envelope
        now = systime.time()
        if not refresh and _screen_cache["data"] and (now - _screen_cache["ts"]) < _SCREEN_TTL:
            return envelope(data=_screen_cache["data"], meta={"cache": "hit", "ttl_remaining_s": int(_SCREEN_TTL - (now - _screen_cache["ts"]))})
        try:
            async with _screen_lock:
                # 拿到锁后再查一次: 可能在等锁期间别的请求已刷新
                now2 = systime.time()
                if not refresh and _screen_cache["data"] and (now2 - _screen_cache["ts"]) < _SCREEN_TTL:
                    return envelope(data=_screen_cache["data"], meta={"cache": "hit", "ttl_remaining_s": int(_SCREEN_TTL - (now2 - _screen_cache["ts"]))})
                data = await _do_screen()
                _screen_cache["data"] = data
                _screen_cache["ts"] = systime.time()
            return envelope(data=data, meta={"cache": "miss", "fresh": True})
        except Exception as e:
            import traceback
            log.error(f"dexin 失败: {e}\n{traceback.format_exc()}")
            # 兜底: stale 缓存
            if _screen_cache["data"]:
                stale = dict(_screen_cache["data"])
                stale["_degraded"] = "stale"
                return envelope(data=stale, error=None, meta={"cache": "stale", "reason": str(e)[:200]})
            return envelope(error=f"dexin 失败: {e}", data={"stages": {k: [] for k in ("cang_zha", "xu_sha", "clearing", "de_xin", "xu_sha_dangerous")},
                                                            "universe_total": 0, "candidate_total": 0,
                                                            "regime": "—", "regime_quote": "—",
                                                            "laws": _LAWS, "disclaimer": _LAWS["disclaimer"],
                                                            "ts": datetime.now().isoformat()})

    @app.get("/api/dexin/laws")
    async def api_dexin_laws():
        from .server import envelope
        return envelope(data=_LAWS)

    @app.get("/api/dexin/check/{code}")
    async def api_dexin_check(code: str):
        """单股走 DexinTrendAgent 判定, 给 dash / 全A 表格右键 / 自选页 用.

        返回:
          {
            code, name,
            phase, variant, stage_label, advice, quote, creed_line,
            phase_dates: {藏诈日, 虚杀日, 洗盘区间, 得鑫日, cycle_days},
            signals: {...},
          }
        """
        from .server import envelope
        import re as _re
        if not _re.fullmatch(r"\d{1,6}", code):
            return envelope(error=f"非法 code: {code}", status_code=400)
        from .. import data_layer
        # 单股拉 60d 日线 (走 cache, fast path)
        try:
            df = await asyncio.wait_for(
                asyncio.to_thread(data_layer.fetch_daily, code, 60),
                timeout=8.0,
            )
        except (asyncio.TimeoutError, Exception) as e:
            log.warning(f"dexin check {code} fetch_daily 失败: {type(e).__name__}: {e}")
            return envelope(error=f"日线拉取失败: {e}", status_code=503)
        if df is None or len(df) < 25:
            return envelope(error=f"日线不足 (got {len(df) if df is not None else 0}, need ≥25)", data={"code": code, "phase": "data_short"})
        # name 简版: 前端会用 _renderName(code) 异步补, 这里只返 code
        name = code
        cls = _classify_stage(df)
        return envelope(data={
            "code": code,
            "name": name,
            "phase": cls.get("phase"),
            "stage": cls.get("stage"),
            "stage_label": cls.get("stage_label"),
            "variant": cls.get("variant"),
            "advice": cls.get("advice"),
            "quote": cls.get("quote"),
            "creed_line": _LAWS["stages"].get(cls.get("stage", ""), {}).get("creed_line"),
            "phase_dates": cls.get("phase_dates") or {},
            "signals": cls.get("signals") or {},
            "ts": datetime.now().isoformat(),
        })

    log.info("得鑫量变术 路由已注册")
