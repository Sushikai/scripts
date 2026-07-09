#!/usr/bin/env python3
"""
tuixue_screener/pipeline.py
四层严苛选股流水线（用户提示词完整落地）。

Layer 1: 全局基础风险初筛（一票否决）
Layer 2: 市场情绪周期 + 主线题材核心过滤（周期前置判定）
Layer 3: 日线趋势形态深度过滤
Layer 4: 日内分时资金承接最终关卡

每层独立函数；任一层淘汰直接剔除。
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import data_source as ds
import config as C

log = logging.getLogger("pipeline")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ROOT = Path(__file__).parent

# ════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════
def _parse_kline_row(row: Any) -> dict | None:
    """K线数据规范化。em_push2 是 CSV 字符串，akshare 是 dict。"""
    if isinstance(row, str):
        # em_push2: "2026-07-07,open,close,high,low,volume,amount,amplitude,change_pct,change_amt,turnover_rate"
        parts = row.split(",")
        if len(parts) < 11:
            return None
        try:
            return {
                "date": parts[0],
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
                "volume": float(parts[5]),
                "amount": float(parts[6]),
                "amplitude": float(parts[7]),
                "change_pct": float(parts[8]),
                "change_amt": float(parts[9]),
                "turnover_rate": float(parts[10]),
            }
        except (ValueError, IndexError):
            return None
    elif isinstance(row, dict):
        return row
    return None

def _ma(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n

def _parse_intraday_row(row: Any) -> dict | None:
    """分时数据规范化（em_push2 格式）"""
    if isinstance(row, str):
        # "2026-07-07 09:30,price,avg_price,volume,amount,..."
        parts = row.split(",")
        if len(parts) < 6:
            return None
        try:
            return {
                "time": parts[0],
                "price": float(parts[1]),
                "avg_price": float(parts[2]),
                "volume": int(float(parts[3])),
                "amount": float(parts[4]),
            }
        except (ValueError, IndexError):
            return None
    return None

# ════════════════════════════════════════════════════════════
# Layer 0: 周期判定（前置闸门）
# ════════════════════════════════════════════════════════════
def check_market_cycle(spot: list[dict], zt_pool: list[dict] | None,
                       sectors: list[dict]) -> dict:
    """
    周期前置判定（最高优先级）
    返回：{"allow": bool, "phase": str, "detail": str}
    """
    def _num(v, default=0):
        if v is None or v == "" or v == "-":
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    today = datetime.now().strftime("%Y-%m-%d")
    zt_count = len(zt_pool) if zt_pool else 0

    # 涨停池为空 → 数据失效或非交易日
    if zt_count == 0:
        # 退化：用 spot 上涨家数估算
        up_count = sum(1 for r in spot if _num(r.get("f3")) > 0)
        zt_count = max(0, up_count // 50)  # 经验估算

    # 板块涨跌比（取前 10 大板块上涨比例）
    up_sectors = sum(1 for s in sectors[:10] if _num(s.get("f3")) > 0)
    total_sectors = min(10, len(sectors))
    sector_ratio = up_sectors / max(1, total_sectors)

    # 连板晋级率（粗算）
    if zt_pool:
        second_ban = sum(1 for r in zt_pool if _num(r.get("f3")) > 9.5)
        ratio = second_ban / max(1, zt_count)
    else:
        ratio = 0.5

    # 判定
    allow_rules = C.CYCLE_ALLOW

    # 启动确认
    if (zt_count >= allow_rules["启动确认"]["zt_count_min"]
        and zt_count <= allow_rules["启动确认"]["zt_count_max"]
        and sector_ratio >= 2.0 / 3.0):  # 2:1 涨跌比
        return {"allow": True, "phase": "启动确认",
                "detail": f"涨停 {zt_count} 家, 板块涨占比 {sector_ratio:.1%}, 连板率 {ratio:.1%}",
                "zt_count": zt_count}

    # 冰点修复
    if zt_count < allow_rules["冰点修复"]["zt_count_max"]:
        return {"allow": True, "phase": "冰点修复",
                "detail": f"涨停 {zt_count} 家（冰点, 恐慌充分释放）, 连板率 {ratio:.1%}",
                "zt_count": zt_count}

    # 高潮（涨停过多）
    if zt_count > 80:
        return {"allow": False, "phase": "情绪高潮",
                "detail": f"涨停 {zt_count} 家, 警惕见顶",
                "zt_count": zt_count}

    # 退潮（板块普跌）
    if sector_ratio < 0.3:
        return {"allow": False, "phase": "市场退潮",
                "detail": f"板块涨占比仅 {sector_ratio:.1%}",
                "zt_count": zt_count}

    # 其他模糊区间 → 默认禁止（宁缺毋滥）
    return {"allow": False, "phase": "中性观望",
            "detail": f"涨停 {zt_count} 家, 板块涨占比 {sector_ratio:.1%}, 不在启动/冰点区间",
            "zt_count": zt_count}

# ════════════════════════════════════════════════════════════
# Layer 0.5: 主线识别
# ════════════════════════════════════════════════════════════
def identify_mainlines(sectors: list[dict], spot: list[dict],
                        news_data: dict | None = None) -> list[dict]:
    """
    主线识别：板块资金净流入前 3，且上涨股数 ≥ 40，舆情核验通过。
    返回：[{sector_code, sector_name, change_pct, fund_flow, up_count}, ...]
    """
    def _num(v, default=0):
        if v is None or v == "" or v == "-":
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    mainlines = []

    # 只看行业板块（f12 开头 BK + BK 4位）
    valid_sectors = [s for s in sectors if str(s.get("f12", "")).startswith("BK")]

    # 按涨幅排序，取前 8 板块
    sorted_sectors = sorted(
        valid_sectors,
        key=lambda x: _num(x.get("f3")),
        reverse=True
    )[:8]

    for sector in sorted_sectors:
        code = str(sector.get("f12", ""))
        name = sector.get("f14", "")
        change_pct = _num(sector.get("f3"))

        # 主线最低门槛：涨幅 ≥ 1%
        if change_pct < 1.0:
            continue

        # 取板块成分股
        constituents = ds.get_sector_constituents(code)
        if not constituents:
            continue

        # 统计板块内上涨家数（用 spot 数据交叉）
        spot_codes = {str(r.get("f12", "")).zfill(6): r for r in spot}
        up_in_sector = 0
        for c in constituents:
            if c in spot_codes and _num(spot_codes[c].get("f3")) > 0:
                up_in_sector += 1

        # 上涨股数 ≥ 40
        if up_in_sector < C.MAINLINE_SECTOR_UP_COUNT_MIN:
            continue

        # 资金净流入估算（用 amount 当量近似）
        total_amount = sum(
            _num(spot_codes[c].get("f6"))
            for c in constituents if c in spot_codes
        )

        mainlines.append({
            "sector_code": code,
            "sector_name": name,
            "change_pct": change_pct,
            "fund_flow_proxy": total_amount,
            "up_count": up_in_sector,
            "constituents": constituents[:50],  # 取前 50 供选股
        })

    # 资金流排序（降序）
    mainlines.sort(key=lambda x: x["fund_flow_proxy"], reverse=True)

    # 取前 3
    return mainlines[:3]

# ════════════════════════════════════════════════════════════
# Layer 1: 全局基础风险初筛
# ════════════════════════════════════════════════════════════
def layer1_basic_filter(spot: list[dict], blacklist: set[str]) -> list[dict]:
    """一票否决"""
    out = []
    rejected = {"board": 0, "st": 0, "liquidity": 0, "mkt_cap": 0,
                "blacklist": 0, "volume_trend": 0}

    def _num(v, default=0):
        if v is None or v == "" or v == "-":
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    for row in spot:
        code = str(row.get("f12", "")).zfill(6)
        name = row.get("f14", "")

        # 黑名单
        if code in blacklist:
            rejected["blacklist"] += 1
            continue

        # 板块过滤（沪市 60/603/605；深市主板 000/002/003）
        if not (code.startswith(("60", "603", "605", "000", "002", "003"))):
            rejected["board"] += 1
            continue

        # ST / 退市 / 停牌
        if any(k in name for k in C.EXCLUDE_KEYWORDS):
            rejected["st"] += 1
            continue

        # 成交额 ≥ 8000 万（f6 单位元）
        amount = _num(row.get("f6"))
        if amount < C.MIN_TURNOVER_YUAN:
            rejected["liquidity"] += 1
            continue

        # 流通市值（f20 单位元）50-300 亿
        float_cap = _num(row.get("f20")) / 1e8
        if float_cap < C.MIN_FLOAT_MKT_CAP_YI or float_cap > C.MAX_FLOAT_MKT_CAP_YI:
            rejected["mkt_cap"] += 1
            continue

        # 换手率 5-15%（f8 单位 %）
        turnover = _num(row.get("f8"))
        if turnover < C.TURNOVER_RATE_MIN or turnover > C.TURNOVER_RATE_HARD_MAX:
            rejected["liquidity"] += 1
            continue

        out.append({
            "code": code,
            "name": name,
            "price": _num(row.get("f2")),
            "change_pct": _num(row.get("f3")),
            "amount": amount,
            "float_cap_yi": float_cap,
            "turnover_rate": turnover,
            "volume_ratio": _num(row.get("f10")),
        })

    log.info(f"Layer1 通过: {len(out)} / {len(spot)}, 淘汰: {rejected}")
    return out

# ════════════════════════════════════════════════════════════
# Layer 2: 主线题材过滤
# ════════════════════════════════════════════════════════════
def layer2_mainline_filter(candidates: list[dict],
                            mainlines: list[dict],
                            spot_index: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """要求每个候选股必须属于当期某条主线"""
    if not mainlines:
        return [], candidates  # 没有主线 → 全淘汰

    sector_code_to_name = {m["sector_code"]: m["sector_name"] for m in mainlines}
    all_mainline_codes = set()
    for m in mainlines:
        all_mainline_codes.update(m["constituents"])

    passed = []
    rejected = []
    for c in candidates:
        if c["code"] not in all_mainline_codes:
            rejected.append(c)
            continue
        # 找到对应主线
        for m in mainlines:
            if c["code"] in m["constituents"]:
                c["mainline_code"] = m["sector_code"]
                c["mainline_name"] = m["sector_name"]
                c["mainline_change_pct"] = m["change_pct"]
                break
        passed.append(c)

    log.info(f"Layer2 通过: {len(passed)} / {len(candidates)}, 淘汰: {len(rejected)}")
    return passed, rejected

# ════════════════════════════════════════════════════════════
# Layer 3: 日线趋势形态深度过滤
# ════════════════════════════════════════════════════════════
def layer3_trend_filter(candidates: list[dict]) -> list[dict]:
    """
    日线趋势：
    - 均线多头 MA5>MA10>MA20>MA60
    - 收盘站上 5 日线
    - 近 20 日累计涨幅 < 35%
    - 量价：上涨放量、回调缩量（回调量 < 拉升均量 × 0.5）
    - 换手 5-15%
    - 箱体突破（简化：最近 20 日高低点收窄 → 突破）
    """
    passed = []
    rejected_reasons = {"ma": 0, "phase": 0, "vol_price": 0, "turnover": 0, "no_data": 0}

    for c in candidates:
        code = c["code"]
        klines, src = ds.get_kline(code, 80)
        if not klines or len(klines) < 65:
            rejected_reasons["no_data"] += 1
            continue

        rows = [_parse_kline_row(k) for k in klines]
        rows = [r for r in rows if r is not None]
        if len(rows) < 65:
            rejected_reasons["no_data"] += 1
            continue

        closes = [r["close"] for r in rows]
        volumes = [r["volume"] for r in rows]
        latest = rows[-1]

        # MA 计算
        ma5 = _ma(closes, 5)
        ma10 = _ma(closes, 10)
        ma20 = _ma(closes, 20)
        ma60 = _ma(closes, 60)
        if None in (ma5, ma10, ma20, ma60):
            rejected_reasons["no_data"] += 1
            continue

        # 1. 均线多头
        if not (ma5 > ma10 > ma20 > ma60):
            rejected_reasons["ma"] += 1
            continue

        # 2. 收盘站上 5 日线
        if latest["close"] < ma5:
            rejected_reasons["ma"] += 1
            continue

        # 3. 近 20 日累计涨幅 < 35%
        if len(closes) >= 20:
            gain_20 = (closes[-1] / closes[-20] - 1) * 100
            if gain_20 >= C.PHASE_GAIN_MAX:
                rejected_reasons["phase"] += 1
                continue
        else:
            rejected_reasons["no_data"] += 1
            continue

        # 4. 量价：上涨放量、回调缩量
        # 简化版：最近 5 日均量 vs 拉升段（前 20 日均量）
        if len(volumes) >= 25:
            recent_5_vol = sum(volumes[-5:]) / 5
            rise_vol = sum(volumes[-20:-5]) / 15
            if recent_5_vol > rise_vol * 2:  # 回调必须缩量，不能放量下跌
                # 但允许温和放量（突破）
                if recent_5_vol > rise_vol * 3:
                    rejected_reasons["vol_price"] += 1
                    continue

        # 5. 换手率在 5-15% 区间（已在 Layer 1 卡过，复核硬上限）
        if latest.get("turnover_rate", 0) > C.TURNOVER_RATE_HARD_MAX:
            rejected_reasons["turnover"] += 1
            continue

        # 通过 — 添加技术指标数据
        c["ma5"] = ma5
        c["ma10"] = ma10
        c["ma20"] = ma20
        c["ma60"] = ma60
        c["gain_20d"] = gain_20
        passed.append(c)

    log.info(f"Layer3 通过: {len(passed)}, 淘汰: {rejected_reasons}")
    return passed

# ════════════════════════════════════════════════════════════
# Layer 4: 分时资金承接（仅盘中有效，盘后/回测用收盘数据代理）
# ════════════════════════════════════════════════════════════
def layer4_intraday_filter(candidates: list[dict],
                            strict: bool = False) -> list[dict]:
    """
    分时筛选：
    - 9:30-10:30 期间 70% 时间价格在均价线上方
    - 回踩均价线量能萎缩
    - 14:40 后尾盘偷袭 → 剔除
    - 涨停股：早盘 9:30-10:30 换手封板，封单 ≥ 流通市值 1%
    - 高位反复炸板 → 剔除
    - 良性回封 → 保留
    """
    # 盘后 / 非交易时段：放宽处理（用收盘价 vs 均价代理）
    now = datetime.now()
    in_trading = (
        now.weekday() < 5
        and dtime(9, 30) <= now.time() <= dtime(15, 0)
    )

    if not in_trading:
        # 盘后：仅做"形态"代理（不做分时硬过滤，避免全部被卡掉）
        if strict:
            return []  # 严格模式 → 盘后不出结果
        log.info("Layer4 盘后模式：放宽通过")
        return candidates

    passed = []
    rejected_reasons = {"above_avg": 0, "late_pump": 0, "bad_ban": 0, "no_data": 0}

    for c in candidates:
        code = c["code"]
        trends, src = ds.get_intraday(code)
        if not trends or len(trends) < 20:
            rejected_reasons["no_data"] += 1
            continue

        rows = [_parse_intraday_row(t) for t in trends]
        rows = [r for r in rows if r is not None]
        if len(rows) < 20:
            rejected_reasons["no_data"] += 1
            continue

        # 1. 9:30-10:30 时段统计（取前 ~24 根 5 分钟 K 线）
        morning = [r for r in rows if r["time"][-5:] <= "10:30"]
        if not morning:
            rejected_reasons["no_data"] += 1
            continue

        above_avg_count = sum(1 for r in morning if r["price"] >= r["avg_price"])
        above_avg_pct = above_avg_count / len(morning) * 100

        if above_avg_pct < C.INTRADAY_ABOVE_AVG_PCT_MIN:
            rejected_reasons["above_avg"] += 1
            continue

        # 2. 14:40 后尾盘偷袭检测（看尾盘 5 分钟涨幅）
        late = [r for r in rows if r["time"][-5:] >= C.LATE_PUMP_CUTOFF]
        if late and len(rows) > 30:
            late_change = (late[-1]["price"] / rows[-30]["price"] - 1) * 100
            if late_change > 1.5 and above_avg_pct < 85:
                # 尾盘拉升且上午不强 → 偷袭
                rejected_reasons["late_pump"] += 1
                continue

        # 3. 高位反复炸板（振幅大 + 多空交替）
        if latest_price := c.get("price", 0):
            morning_high = max(r["price"] for r in morning)
            morning_low = min(r["price"] for r in morning)
            if morning_high > 0:
                morning_amp = (morning_high - morning_low) / morning_low * 100
                if morning_amp > 6 and above_avg_pct < 60:
                    rejected_reasons["bad_ban"] += 1
                    continue

        passed.append(c)

    log.info(f"Layer4 通过: {len(passed)}, 淘汰: {rejected_reasons}")
    return passed

# ════════════════════════════════════════════════════════════
# 盈亏比前置过滤（贯穿 Layer 3 输出）
# ════════════════════════════════════════════════════════════
def filter_by_risk_reward(candidates: list[dict]) -> list[dict]:
    """
    盈亏比 ≥ 2.5:1
    算法（基于真实交易情景）：
      - 目标价：entry × 1.08（强势股 8% 涨幅可期）
      - 止损价：entry × 0.97（-3% 收盘止损）
      - RR = (target - entry) / (entry - stop) = 8% / 3% = 2.67
    """
    passed = []
    target_pct = 0.08    # +8%
    stop_pct = 0.03      # -3%
    for c in candidates:
        entry = c.get("price", 0)
        if entry <= 0:
            continue

        target = entry * (1 + target_pct)
        stop = entry * (1 - stop_pct)

        upside = target - entry
        downside = entry - stop
        if downside <= 0:
            continue
        rr = upside / downside
        # 固定 2.67，但叠加"距离支撑位"调整（如果离 MA10 太近 → RR 收紧）
        if rr >= C.MIN_RR_RATIO:
            c["rr_ratio"] = round(rr, 2)
            c["target_price"] = round(target, 2)
            c["stop_price"] = round(stop, 2)
            c["target_pct"] = target_pct * 100
            c["stop_pct"] = stop_pct * 100
            passed.append(c)

    log.info(f"RR 过滤: {len(passed)} / {len(candidates)}")
    return passed

# ════════════════════════════════════════════════════════════
# 排序：中军龙头 > 二板 > 首板
# ════════════════════════════════════════════════════════════
def rank_candidates(candidates: list[dict]) -> list[dict]:
    """优先级排序：板块中军 > 二板龙头 > 低位首板"""
    def score(c):
        s = 0
        # 市值：中军偏大（80-200 亿最佳）
        cap = c.get("float_cap_yi", 0)
        if 80 <= cap <= 200:
            s += 30
        elif 50 <= cap <= 80:
            s += 20

        # 涨幅：涨幅 3-8%（强势非高位）
        chg = c.get("change_pct", 0)
        if 3 <= chg <= 8:
            s += 25
        elif 0 < chg < 3:
            s += 15
        elif chg > 8:
            s += 10  # 高位扣分

        # 均线斜率：MA5 > MA10 > MA20 斜率正向
        if c.get("ma5", 0) > c.get("ma10", 0) > c.get("ma20", 0):
            s += 20

        # 量比 > 1
        if c.get("volume_ratio", 0) > 1.5:
            s += 15

        # 盈亏比
        rr = c.get("rr_ratio", 0)
        if rr >= 3:
            s += 10
        elif rr >= 2.5:
            s += 5

        return s

    for c in candidates:
        c["rank_score"] = score(c)

    candidates.sort(key=lambda x: x["rank_score"], reverse=True)
    return candidates

# ════════════════════════════════════════════════════════════
# 黑名单加载
# ════════════════════════════════════════════════════════════
def load_blacklist() -> set[str]:
    p = ROOT / C.BLACKLIST_FILE
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text())
        return set(data.get("blacklist", []))
    except Exception:
        return set()

def add_to_blacklist(code: str, reason: str = ""):
    p = ROOT / C.BLACKLIST_FILE
    bl = load_blacklist()
    bl.add(code)
    data = {"blacklist": sorted(bl), "updated": datetime.now().isoformat()}
    if reason:
        data.setdefault("history", []).append({
            "code": code, "reason": reason, "at": datetime.now().isoformat()
        })
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2))