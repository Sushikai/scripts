"""
zt_signal.py — 涨停板次日溢价策略 统一选股逻辑 (回测与实时推票共用同一套)。

2026-08-09 重构:
  之前回测 (zt_backtest._score_zt_candidate + 过滤硬编码) 和推票 (zt_screener._score_one/_passes)
  是两套不同逻辑, 字段维度不一致, 打分权重硬编码不可训练。
  现在统一成一套 passes() + score_one(), 权重全部参数化 (纳入进化算法训练)。

维度口径:
  - 回测: 从 daily_cache 构造字段 (连板 streak / 换手 turnover_pct / 成交额 amount /
          涨跌幅 change_pct / N日涨停次数 / 股本近似市值)。封单/首封/炸板历史不可得 → 0。
  - 推票: 走 fetch_zt_pool 涨停池全字段 (封单/首封/炸板/市值)。同一套 score_one 复用。

权重约定 (WEIGHTS):
  所有项在 score_one 里线性叠加。权重可由优化器训练 (zt_optimizer 的 WEIGHT_GRID)。
  回测训练时, 历史不可得维度的字段为 0 → 对应权重在回测里中性;
  推票时字段全 → 权重真实生效。这样同一套权重跨回测/推票一致。
"""
from __future__ import annotations

from typing import Any

# ── 股本快照缓存 (2026-08-09): 历史市值近似 ─────────────────
#   东财 push2delay clist 一次拉全市场 f12(代码)/f14(名称)/f2(最新价)/f21(流通市值)。
#   流通股本 = 流通市值 / 最新价。历史市值 ≈ 流通股本 × 历史收盘价。
#   缓存到 /tmp/zt_shares_snapshot.json, TTL 24h (股本变动低频)。
_SHARES_CACHE_PATH = "/tmp/zt_shares_snapshot.json"
_SHARES_TTL_SEC = 24 * 3600


def load_shares_snapshot(force: bool = False) -> dict[str, float]:
    """返回 {code6: 流通股本(股)}。缓存优先, 过期/缺失则拉取。失败返空 dict。"""
    import json
    import os
    import time as _t
    try:
        if not force and os.path.exists(_SHARES_CACHE_PATH):
            age = _t.time() - os.path.getmtime(_SHARES_CACHE_PATH)
            if age < _SHARES_TTL_SEC:
                with open(_SHARES_CACHE_PATH) as f:
                    return json.load(f)
    except Exception:
        pass
    out: dict[str, float] = {}
    try:
        import requests
        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        for pn in range(1, 61):
            params = {
                "pn": pn, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                "fields": "f2,f12,f14,f21",
            }
            r = requests.get(url, params=params, timeout=10,
                             headers={"User-Agent": "Mozilla/5.0"})
            diff = (r.json().get("data") or {}).get("diff") or []
            if not diff:
                break
            for it in diff:
                code = str(it.get("f12", "")).zfill(6)
                price = float(it.get("f2", 0) or 0)
                circ = float(it.get("f21", 0) or 0)
                if code and price > 0 and circ > 0:
                    out[code] = circ / price  # 流通股本(股)
            if len(diff) < 100:
                break
        if out:
            with open(_SHARES_CACHE_PATH, "w") as f:
                json.dump(out, f)
    except Exception:
        pass
    return out


def approx_mcap_yi(code: str, close_price: float,
                   shares: dict[str, float] | None = None) -> float:
    """历史流通市值近似 (亿) = 流通股本 × 历史收盘价。无股本 → 0 (中性)。"""
    if not shares or close_price <= 0:
        return 0.0
    s = shares.get(str(code).zfill(6), 0)
    if not s:
        return 0.0
    return round(s * close_price / 1e8, 2)


# ── 打分权重默认值 (可被优化器覆盖) ──────────────────────────
# 每项: {因子名: 基准权重}。总 17 维 (2026-08-10 R65 扩展: 新增 8 维)。
DEFAULT_WEIGHTS: dict[str, float] = {
    # 原有 9 维
    "streak":     25.0,   # 连板梯度: 首板低分, 2/3/4/5+ 梯度加分
    "burst":      8.0,    # 每炸板一次扣分 (负向)
    "seal":       15.0,   # 封单金额分档 (推票字段; 回测=0)
    "first_time": 10.0,   # 首封时间分档 (推票字段; 回测=0)
    "pct":        5.0,    # 涨幅到位加分
    "vol_ratio":  5.0,    # 量比 ≥3 加分 (回测无量比 → 0)
    "mcap":       5.0,    # 小盘 15-100亿 加分
    "turnover":   8.0,    # 换手适中加分 (5-15 最佳)
    "amount":     5.0,    # 成交额 ≥2亿 加分
    # 2026-08-10 R65 新增 8 维 (训练可观测, 回测有字段)
    "trend_5d":      6.0,  # 5 日趋势强度 (close/MA5 偏离度)
    "pct_chg_t1":    4.0,  # T+1 开盘涨跌幅加权 (推票字段)
    "gap_premium":   5.0,  # 隔夜溢价历史均值
    "north_proxy":   4.0,  # 北向代理 (大资金流入, 推票字段)
    "sector_heat":   5.0,  # 板块热度 (推票字段, 回测=0)
    "index_align":   3.0,  # 与指数同向 (推票字段, 回测=0)
    "vol_amount":    3.0,  # 量额比异常 (volume/amount 偏离)
    "ma_converge":   3.0,  # 均线收敛度 (MA5/MA10 距离)
    # 2026-08-11 R66 新增 5 维 K 线形态 (回测有字段)
    "ma_align":      5.0,  # 多头排列: 收盘 > MA5 > MA10 > MA20
    "vol_trend":     6.0,  # 量能爆发倍数 (涨停日 vol / 5日均 vol)
    "upper_shadow":  4.0,  # 上影线比例 (短上影 = T 字板; 长上影 = 高位抛压)
    "body_at_pct":   5.0,  # 实体比 (实体长 = 真突破)
    # 2026-08-11 R67 新增 12 维 技术指标 + 量价 + 时间效应 + 题材晋级
    "kdj":           4.0,  # KDJ_K 高位超买扣分, 低位金叉加分
    "rsi":           4.0,  # RSI6 70+ 超买扣分, <30 超卖反弹加分
    "boll":          3.0,  # BOLL 上轨突破/中轨支撑得分
    "macd":          4.0,  # MACD DIF 上穿零轴=多, 下穿=空
    "gap_jump":      3.0,  # 跳空缺口 (突破信号 +)
    "yang_bao_yin":  3.0,  # 长阳包阴 反包形态 +
    "vp_dir":        3.0,  # 量价同向天数 (齐升 +)
    "vol_shrink_zt": 4.0,  # 缩量涨停 (筹码锁定 +)
    "vol_top_div":   3.0,  # 量价背离 (高位放量滞涨 -)
    "vol_step":      3.0,  # 量能台阶 (逐步放量 +)
    "weekday":       2.0,  # 周一/周五效应 (推票字段)
    "month_phase":   2.0,  # 月初/月末效应 (推票字段)
    "report_window": 2.0,  # 季报窗口避雷 (-)
    "promote_ratio": 5.0,  # 题材晋级率 (高 = 题材活跃)
    "prev_zt_perf":  5.0,  # 昨日涨停表现 (高开高走溢价)
    "strong_zt":     4.0,  # 强势涨停占比 (strength信号)
}

# 权重优化搜索空间 (每维可选值) — 纳入 zt_optimizer
WEIGHT_GRID: dict[str, list[float]] = {
    # 原有 9 维
    "streak":      [15.0, 20.0, 25.0, 30.0, 35.0],
    "burst":       [5.0, 8.0, 10.0, 15.0],
    "seal":        [5.0, 10.0, 15.0, 20.0, 25.0],
    "first_time":  [5.0, 10.0, 15.0],
    "pct":         [0.0, 5.0, 8.0, 10.0],
    "vol_ratio":   [0.0, 5.0, 8.0],
    "mcap":        [0.0, 5.0, 8.0],
    "turnover":    [5.0, 8.0, 10.0, 15.0],
    "amount":      [0.0, 5.0, 8.0],
    # 2026-08-10 R65 新增 8 维
    "trend_5d":    [0.0, 3.0, 6.0, 10.0],
    "pct_chg_t1":  [0.0, 4.0, 8.0],
    "gap_premium": [0.0, 5.0, 10.0],
    "north_proxy": [0.0, 4.0, 8.0],
    "sector_heat": [0.0, 5.0, 10.0],
    "index_align": [0.0, 3.0, 6.0],
    "vol_amount":  [0.0, 3.0, 6.0],
    "ma_converge": [0.0, 3.0, 6.0],
    # 2026-08-11 R66 新增 4 维 K 线形态权重
    "ma_align":    [0.0, 5.0, 10.0],
    "vol_trend":   [0.0, 6.0, 12.0],
    "upper_shadow":[0.0, 4.0, 8.0],
    "body_at_pct": [0.0, 5.0, 10.0],
    # 2026-08-11 R67 新增 12 维 技术指标 + 量价 + 时间效应
    "kdj":           [0.0, 4.0, 8.0],
    "rsi":           [0.0, 4.0, 8.0],
    "boll":          [0.0, 3.0, 6.0],
    "macd":          [0.0, 4.0, 8.0],
    "gap_jump":      [0.0, 3.0, 6.0],
    "yang_bao_yin":  [0.0, 3.0, 6.0],
    "vp_dir":        [0.0, 3.0, 6.0],
    "vol_shrink_zt": [0.0, 4.0, 8.0],
    "vol_top_div":   [0.0, 3.0, 6.0],
    "vol_step":      [0.0, 3.0, 6.0],
    "weekday":       [0.0, 2.0, 4.0],
    "month_phase":   [0.0, 2.0, 4.0],
    "report_window": [0.0, 2.0, 4.0],
    "promote_ratio": [0.0, 5.0, 10.0],
    "prev_zt_perf":  [0.0, 5.0, 10.0],
    "strong_zt":     [0.0, 4.0, 8.0],
}

# 回测训练时可观测的权重维度 (日线字段可直接构造):
#   streak / pct / mcap / turnover / amount / trend_5d / gap_premium / vol_amount / ma_converge
#   + 2026-08-11 R66: ma_align / vol_trend / upper_shadow / body_at_pct
#   封单 seal / 首封 first_time / 炸板 burst / 量比 vol_ratio / T+1涨跌幅 pct_chg_t1
#   北向 north_proxy / 板块 sector_heat / 指数 index_align — 历史不可得 → 字段为 0 中性
BACKTEST_TRAINABLE_WEIGHTS: tuple[str, ...] = (
    "streak", "pct", "mcap", "turnover", "amount",
    "trend_5d", "gap_premium", "vol_amount", "ma_converge",
    "ma_align", "vol_trend", "upper_shadow", "body_at_pct",
    # 2026-08-11 R67 新增 (回测 enrich 后字段齐全, 历史可得)
    "kdj", "rsi", "boll", "macd", "gap_jump",
    "yang_bao_yin", "vp_dir", "vol_shrink_zt",
    "promote_ratio", "prev_zt_perf", "strong_zt",
    # 历史不可得 (推票字段, 训练时为 0 中性): weekday/month_phase/report_window
)

# 20cm 板 (创业板/科创板)
_20CM_PREFIXES = ("300", "301", "688", "689")


def is_20cm(code: str) -> bool:
    return str(code).zfill(6)[:3] in _20CM_PREFIXES


def board_of(code: str) -> str:
    """返回 board_filter 用值: main/gem/star/gem+star。"""
    code = str(code).zfill(6)
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("300", "301")):
        return "gem"
    return "main"


def passes(it: dict, params: dict) -> tuple[bool, str]:
    """统一过滤。it 是候选 dict (回测/推票都喂同结构)。返回 (通过, 原因)。

    字段口径:
      streak(int), burst_count(int, -1=未知), first_time(str ""=未知),
      market_cap(float 亿, 0=未知), turnover_pct(float, 0=未知),
      limit_order_amount(float 亿, 0=未知), change_pct(float 可选),
      board_filter(str), vol_ratio(float, 0=未知), burst_3d(int, 累计炸板),
      is_yiziban(bool), is_st(bool), pct_chg_5d(float), open_t1_pct(float)
    """
    code = str(it.get("code", "")).zfill(6)
    board_filter = str(params.get("board_filter", "all") or "all")

    # ── 板块过滤 ──
    if board_filter == "main" and is_20cm(code):
        return False, "20cm 板 (开 T+1 风险大)"
    if board_filter == "gem" and board_of(code) != "gem":
        return False, "非创业板"
    if board_filter == "star" and board_of(code) != "star":
        return False, "非科创板"
    if board_filter == "gem+star" and not is_20cm(code):
        return False, "非20cm"

    # ── 排除 ST 股 (2026-08-10 R65) ──
    if params.get("is_st_exclude", True) and it.get("is_st"):
        return False, "ST 股"

    # ── 连板数 ──
    ms = int(params.get("min_streak", 1) or 1)
    xs = int(params.get("max_streak", 99) or 99)
    streak = int(it.get("streak", 1) or 1)
    if streak < ms:
        return False, f"streak={streak} < {ms}"
    if streak > xs:
        return False, f"streak={streak} > {xs}"

    # ── 炸板次数 (未知 -1 跳过) ──
    bm = int(params.get("burst_max", 99) or 99)
    bc = int(it.get("burst_count", -1) or -1)
    if bc >= 0 and bc > bm:
        return False, f"炸板 {bc} > {bm}"

    # ── 3 日累计炸板 (2026-08-10 R65, 防多次开板妖股) ──
    b3 = int(params.get("burst_window_3d", 99) or 99)
    b3_act = int(it.get("burst_3d", -1) or -1)
    if b3_act >= 0 and b3_act > b3:
        return False, f"3日累计炸板 {b3_act} > {b3}"

    # ── 封单金额 (未知 0 → 跳过, 回测字段历史不可得) ──
    lom = float(params.get("limit_order_min_yi", 0) or 0)
    lo = float(it.get("limit_order_amount", 0) or 0)
    if lo > 0 and lo < lom:
        return False, f"封单 {lo:.1f}亿 < {lom}亿"

    # ── 封板时间 (未知 "" → 跳过) ──
    sb = str(params.get("sealed_before", "") or "")
    ft = str(it.get("first_time", "") or "")
    if sb and ft and len(ft) >= 5 and ft[:5] > sb[:5]:
        return False, f"首封 {ft} > {sb}"

    # ── 市值窗口 (未知 0 → 跳过) ──
    mc_min = float(params.get("mcap_min_yi", 0) or 0)
    mc_max = float(params.get("mcap_max_yi", 1e18) or 1e18)
    mc = float(it.get("market_cap", 0) or 0)
    if params.get("mcap_strict", False):
        # 严格模式: 15-50 亿
        if mc > 0 and not (15.0 <= mc <= 50.0):
            return False, f"严格市值 {mc:.1f}亿 不在 [15,50]"
    elif mc > 0 and not (mc_min <= mc <= mc_max):
        return False, f"市值 {mc:.1f}亿 不在 [{mc_min},{mc_max}]"

    # ── 换手窗口 (未知 0 → 跳过) ──
    to_min = float(params.get("turnover_min_pct", 0) or 0)
    to_max = float(params.get("turnover_max_pct", 99) or 99)
    to = float(it.get("turnover_pct", 0) or 0)
    if to > 0 and not (to_min <= to <= to_max):
        return False, f"换手 {to:.1f}% 不在 [{to_min},{to_max}]"

    # ── 量比下限 (2026-08-10 R65, 回测无 → 跳过) ──
    vr_min = float(params.get("vol_ratio_min", 0) or 0)
    vr = float(it.get("vol_ratio", 0) or 0)
    if vr > 0 and vr < vr_min:
        return False, f"量比 {vr:.1f} < {vr_min}"

    # ── 涨幅最低 (2026-08-10 R65, 避免假涨停) ──
    pct_min = float(params.get("limit_up_pct_min", 9.5) or 9.5)
    pct = float(it.get("change_pct", 0) or 0)
    if pct > 0 and pct < pct_min:
        return False, f"涨幅 {pct:.2f}% < {pct_min}%"

    # ── 一字板要求 (2026-08-10 R65) ──
    if params.get("yiziban_required", False) and not it.get("is_yiziban", False):
        return False, "非一字板"

    # ── T+1 开盘高开要求 (2026-08-10 R65, 回测有字段) ──
    if params.get("gap_open_required", False):
        op = float(it.get("open_t1_pct", -999) or -999)
        if op > -900 and op < 1.0:
            return False, f"T+1 开盘 {op:.2f}% < 1%"

    # ── 5 日累计涨幅窗口 (2026-08-10 R65, 防过度追高) ──
    pc5 = float(it.get("pct_chg_5d", 0) or 0)
    pc5_max = float(params.get("pct_chg_5d_max", 999) or 999)
    pc5_min = float(params.get("pct_chg_5d_min", 0) or 0)
    if pc5 != 0 and not (pc5_min <= pc5 <= pc5_max):
        return False, f"5日累计 {pc5:.1f}% 不在 [{pc5_min},{pc5_max}]"

    # ── 2026-08-11 R66: K 线形态过滤 (回测 enrich 后字段齐全) ──

    # ma_align: 多头排列要求 (-1=空, 0=无, 1=多头)
    if params.get("ma_align_required", 0):
        ma_align = int(it.get("ma_align", 0) or 0)
        required = int(params.get("ma_align_required", 0))
        if ma_align < required:
            return False, f"均线非多头 (ma_align={ma_align} < {required})"

    # trend_5d 最低 (涨停前 5 日趋势强度)
    trend5_min = float(params.get("trend_5d_min", -999) or -999)
    trend5 = float(it.get("trend_5d", 0) or 0)
    if trend5 != 0 and trend5 < trend5_min:
        return False, f"5日趋势 {trend5:.2f}% < {trend5_min}%"

    # vol_trend 下限 (量能爆发倍数, 涨停日 vol / 5日均 vol)
    vt_min = float(params.get("vol_trend_min", 0) or 0)
    vt = float(it.get("vol_trend", 0) or 0)
    if vt > 0 and vt < vt_min:
        return False, f"量能 {vt:.2f}x < {vt_min}x"

    # upper_shadow 上限 (上影线长 → 高位抛压重, 减分)
    us_max = float(params.get("upper_shadow_max", 999) or 999)
    us = float(it.get("upper_shadow", 0) or 0)
    if us > 0 and us > us_max:
        return False, f"上影 {us:.2f}% > {us_max}%"

    # body_at_pct 下限 (实体长 → 真突破, 不限一字板 open=close=high)
    ba_min = float(params.get("body_at_pct_min", 0) or 0)
    ba = float(it.get("body_at_pct", 0) or 0)
    if ba > 0 and ba < ba_min:
        return False, f"实体 {ba:.2f}% < {ba_min}%"

    # ma_converge 上限 (MA5/MA10 距离 → 收敛 vs 发散)
    mac_max = float(params.get("ma_converge_max", 999) or 999)
    mac = float(it.get("ma_converge", 99) or 99)
    if mac < 90 and mac > mac_max:
        return False, f"均线发散 {mac:.2f}% > {mac_max}%"

    # ── 2026-08-11 R67: 技术指标过滤 ──

    # KDJ K 值上限 (超买扣分; 默认不限)
    kdj_max = float(params.get("kdj_k_max", 999) or 999)
    kdj_v = float(it.get("kdj_k", 50.0) or 50.0)
    if kdj_v > 0 and kdj_v > kdj_max:
        return False, f"KDJ_K={kdj_v:.1f} > {kdj_max} (超买)"

    # RSI6 上限 (超买扣分)
    rsi_max = float(params.get("rsi_6_max", 999) or 999)
    rsi_v = float(it.get("rsi_6", 50.0) or 50.0)
    if rsi_v > 0 and rsi_v > rsi_max:
        return False, f"RSI6={rsi_v:.1f} > {rsi_max} (超买)"

    # BOLL 距上轨距离下限 (突破上轨加分, 默认不限)
    boll_min = float(params.get("boll_dist_upper_min", -999) or -999)
    bolu = float(it.get("boll_dist_upper", 0) or 0)
    if bolu != 0 and bolu < boll_min:
        return False, f"BOLL上轨 {bolu:.2f}% < {boll_min}%"

    # MACD DIF ≥ 0 要求 (多头区间)
    macd_dif_min = float(params.get("macd_dif_min", -999) or -999)
    macd_v = float(it.get("macd_dif", 0) or 0)
    if macd_v != 0 and macd_v < macd_dif_min:
        return False, f"MACD_DIF={macd_v:.4f} < {macd_dif_min}"

    # MACD 5 日变化方向: 1=走强, -1=走弱 (0=中性跳过)
    if params.get("macd_dif_chg_required", 0):
        chg_req = int(params.get("macd_dif_chg_required", 0))
        chg_v = float(it.get("macd_dif_5d_chg", 0) or 0)
        if chg_v != 0:
            if chg_req > 0 and chg_v < 0:
                return False, f"MACD 5d 转弱 ({chg_v:.3f})"
            elif chg_req < 0 and chg_v > 0:
                return False, f"MACD 5d 转强 ({chg_v:.3f})"

    # 跳空缺口下限 (突破信号)
    gap_min = float(params.get("gap_pct_min", -999) or -999)
    gap_v = float(it.get("gap_pct", 0) or 0)
    if gap_v != 0 and gap_v < gap_min:
        return False, f"跳空 {gap_v:.2f}% < {gap_min}%"

    # 量价同向天数下限 (齐升信号)
    vp_min = int(params.get("vp_same_dir_min", 0) or 0)
    vp_v = int(it.get("vp_same_dir_n", 0) or 0)
    if vp_min > 0 and vp_v < vp_min:
        return False, f"量价同向 {vp_v}天 < {vp_min}"

    # 缩量涨停要求
    if params.get("vol_shrink_required", False):
        vs_v = int(it.get("vol_shrink_zt", 0) or 0)
        if not vs_v:
            return False, "非缩量涨停"

    # 量价背离过滤 (放量滞涨/价跌量增)
    vtd_max = int(params.get("vol_top_div_max", 999) or 999)
    vtd_v = int(it.get("vol_top_div", 0) or 0)
    if vtd_v > vtd_max:
        return False, f"量价背离 {vtd_v} > {vtd_max}"

    # 量能台阶下限 (逐步放量信号)
    vstep_min = float(params.get("vol_step_min", 0) or 0)
    vstep_v = float(it.get("vol_step_max", 0) or 0)
    if vstep_v > 0 and vstep_v < vstep_min:
        return False, f"量能台阶 {vstep_v:.2f} < {vstep_min}"

    # 题材晋级率下限 (高 = 题材活跃; 2026-08-11 R67)
    pr_min = float(params.get("promote_ratio_min", 0) or 0)
    pr_v = float(it.get("promote_ratio", 0) or 0)
    if pr_v > 0 and pr_v < pr_min:
        return False, f"晋级率 {pr_v:.2f}% < {pr_min}%"

    # 昨日涨停表现下限 (high = 题材溢价强)
    pz_min = float(params.get("prev_zt_avg_ret_min", -999) or -999)
    pz_v = float(it.get("prev_zt_avg_ret", 0) or 0)
    if pz_v != 0 and pz_v < pz_min:
        return False, f"昨日涨停均收益 {pz_v:.2f}% < {pz_min}%"

    # 强势涨停占比下限 (high strength)
    szr_min = float(params.get("strong_zt_ratio_min", 0) or 0)
    szr_v = float(it.get("strong_zt_ratio", 0) or 0)
    if szr_v > 0 and szr_v < szr_min:
        return False, f"强势涨停占比 {szr_v:.2f}% < {szr_min}%"

    # 长阳包阴要求 (反包形态)
    if params.get("yang_bao_yin_required", False):
        yby = int(it.get("long_yang_bao_yin", 0) or 0)
        if not yby:
            return False, "非长阳包阴"

    # 周一/周五过滤 (-1=全部; 默认不限)
    wd_allow = str(params.get("weekday_allow", "all") or "all")
    wd_v = int(it.get("weekday", -1) or -1)
    if wd_allow != "all" and wd_v >= 0:
        if wd_allow == "mon" and wd_v != 0:
            return False, f"非周一 ({wd_v})"
        if wd_allow == "fri" and wd_v != 4:
            return False, f"非周五 ({wd_v})"
        if wd_allow == "wed" and wd_v != 2:
            return False, f"非周三 ({wd_v})"

    # 季报窗口避雷 (1=窗口期)
    if params.get("avoid_report_window", False):
        rw = int(it.get("report_window", 0) or 0)
        if rw:
            return False, "季报窗口期"

    return True, ""


def score_one(it: dict, params: dict | None = None,
              weights: dict[str, float] | None = None) -> float:
    """统一加权打分 (线性叠加)。权重默认 DEFAULT_WEIGHTS, 可由训练覆盖。

    所有项对"字段缺失/未知 (0 或空)"自动中性 (不加分不减分)。
    2026-08-10 R65: 扩 17 维权重 (新增 trend_5d / pct_chg_t1 / gap_premium
                   / north_proxy / sector_heat / index_align / vol_amount / ma_converge)。
    """
    params = params or {}
    weights = weights or DEFAULT_WEIGHTS
    s = 0.0

    # 连板梯度
    w = weights.get("streak", DEFAULT_WEIGHTS["streak"])
    streak = int(it.get("streak", 1) or 1)
    # 基准: 首板 = 0.35*w, 2板 = 0.8*w, 3板 = 1.0*w, 4板 = 0.9*w, 5+ = 0.7*w
    if streak == 1:
        s += 0.35 * w
    elif streak == 2:
        s += 0.8 * w
    elif streak == 3:
        s += 1.0 * w
    elif streak <= 5:
        s += 0.9 * w
    else:
        s += 0.7 * w

    # 炸板惩罚 (未知 -1 → 中性)
    bc = int(it.get("burst_count", -1) or -1)
    if bc >= 0:
        s -= weights.get("burst", DEFAULT_WEIGHTS["burst"]) * max(0, bc)

    # 封单分档 (0 → 中性)
    lo = float(it.get("limit_order_amount", 0) or 0)
    if lo >= 3.0:
        s += 1.0 * weights.get("seal", DEFAULT_WEIGHTS["seal"])
    elif lo >= 1.0:
        s += 0.7 * weights.get("seal", DEFAULT_WEIGHTS["seal"])
    elif lo >= 0.3:
        s += 0.35 * weights.get("seal", DEFAULT_WEIGHTS["seal"])

    # 首封时间 (空 → 中性)
    ft = str(it.get("first_time", "") or "")
    if ft and len(ft) >= 5:
        hm = ft[:5]
        if hm <= "09:30":
            s += 1.0 * weights.get("first_time", DEFAULT_WEIGHTS["first_time"])
        elif hm <= "10:00":
            s += 0.5 * weights.get("first_time", DEFAULT_WEIGHTS["first_time"])
        elif hm <= "11:30":
            s += 0.25 * weights.get("first_time", DEFAULT_WEIGHTS["first_time"])

    # 涨幅到位 (未知 0 → 中性)
    pct = float(it.get("change_pct", 0) or 0)
    if pct > 0:
        threshold = 19.5 if is_20cm(str(it.get("code", ""))) else 9.5
        if pct >= threshold:
            s += weights.get("pct", DEFAULT_WEIGHTS["pct"])

    # 量比 (回测无 → 0 中性)
    vr = float(it.get("vol_ratio", 0) or 0)
    if vr >= 3.0:
        s += weights.get("vol_ratio", DEFAULT_WEIGHTS["vol_ratio"])

    # 市值小盘加分 (0 → 中性)
    mc = float(it.get("market_cap", 0) or 0)
    if 15 <= mc <= 100:
        s += weights.get("mcap", DEFAULT_WEIGHTS["mcap"])

    # 换手适中 (0 → 中性)
    to = float(it.get("turnover_pct", 0) or 0)
    if to > 0:
        if 5 <= to <= 15:
            s += 1.0 * weights.get("turnover", DEFAULT_WEIGHTS["turnover"])
        elif 3 <= to <= 25:
            s += 0.6 * weights.get("turnover", DEFAULT_WEIGHTS["turnover"])
        elif to > 25:
            s -= 0.3 * weights.get("turnover", DEFAULT_WEIGHTS["turnover"])

    # 成交额 ≥2 亿 (0 → 中性)
    am = float(it.get("amount", 0) or 0)
    if am >= 2e8:  # 元
        s += weights.get("amount", DEFAULT_WEIGHTS["amount"])

    # ── 2026-08-10 R65 新增 8 维 ──

    # trend_5d: 5 日趋势 (close / MA5 - 1)
    trend5 = float(it.get("trend_5d", 0) or 0)
    if trend5 != 0:
        # 强趋势 (>5%) 1.0×, 中等 (0-5%) 0.5×, 弱/空 → 中性
        if trend5 >= 5.0:
            s += 1.0 * weights.get("trend_5d", DEFAULT_WEIGHTS["trend_5d"])
        elif trend5 >= 0:
            s += 0.5 * weights.get("trend_5d", DEFAULT_WEIGHTS["trend_5d"])
        else:
            s -= 0.3 * weights.get("trend_5d", DEFAULT_WEIGHTS["trend_5d"])

    # pct_chg_t1: T+1 开盘涨跌幅 (回测有, 推票字段)
    pct_t1 = float(it.get("open_t1_pct", -999) or -999)
    if pct_t1 > -900:
        # 高开 (>2%) 加分, 低开 (<-2%) 减分
        if pct_t1 >= 2.0:
            s += 1.0 * weights.get("pct_chg_t1", DEFAULT_WEIGHTS["pct_chg_t1"])
        elif pct_t1 >= 0:
            s += 0.5 * weights.get("pct_chg_t1", DEFAULT_WEIGHTS["pct_chg_t1"])
        elif pct_t1 < -2.0:
            s -= 0.5 * weights.get("pct_chg_t1", DEFAULT_WEIGHTS["pct_chg_t1"])

    # gap_premium: 隔夜溢价历史均值 (回测有, 推票字段)
    gap_p = float(it.get("gap_premium", 0) or 0)
    if gap_p != 0:
        # 正溢价 > 1% 加分
        if gap_p >= 1.0:
            s += 1.0 * weights.get("gap_premium", DEFAULT_WEIGHTS["gap_premium"])
        elif gap_p >= 0:
            s += 0.4 * weights.get("gap_premium", DEFAULT_WEIGHTS["gap_premium"])
        else:
            s -= 0.3 * weights.get("gap_premium", DEFAULT_WEIGHTS["gap_premium"])

    # north_proxy: 北向代理 (回测=0 中性, 推票有)
    np_v = float(it.get("north_net_yi", 0) or 0)
    if np_v != 0:
        if np_v >= 30.0:
            s += 1.0 * weights.get("north_proxy", DEFAULT_WEIGHTS["north_proxy"])
        elif np_v >= 0:
            s += 0.4 * weights.get("north_proxy", DEFAULT_WEIGHTS["north_proxy"])
        else:
            s -= 0.5 * weights.get("north_proxy", DEFAULT_WEIGHTS["north_proxy"])

    # sector_heat: 板块热度 (推票字段, 回测=0)
    sh_v = float(it.get("sector_heat", 0) or 0)
    if sh_v > 0:
        if sh_v >= 5.0:
            s += 1.0 * weights.get("sector_heat", DEFAULT_WEIGHTS["sector_heat"])
        elif sh_v >= 2.0:
            s += 0.5 * weights.get("sector_heat", DEFAULT_WEIGHTS["sector_heat"])

    # index_align: 与指数同向 (推票字段, 回测=0)
    ia = float(it.get("index_align", 0) or 0)
    if ia > 0:
        s += 1.0 * weights.get("index_align", DEFAULT_WEIGHTS["index_align"])

    # vol_amount: 量额比异常 (volume/amount, 回测有)
    va = float(it.get("vol_amount", 0) or 0)
    if va > 0:
        # 量额比 < 0.05 (低质量) 减分, > 0.15 (异常活跃) 加分
        if va >= 0.15:
            s += 1.0 * weights.get("vol_amount", DEFAULT_WEIGHTS["vol_amount"])
        elif va <= 0.03:
            s -= 0.5 * weights.get("vol_amount", DEFAULT_WEIGHTS["vol_amount"])

    # ma_converge: 均线收敛度 (MA5 - MA10 距离百分比绝对值小 → 收敛 → 加分)
    mac_v = float(it.get("ma_converge", 99) or 99)
    if 0 <= mac_v < 99:
        # < 1% 收敛强加分, > 3% 发散减分
        if mac_v < 1.0:
            s += 1.0 * weights.get("ma_converge", DEFAULT_WEIGHTS["ma_converge"])
        elif mac_v < 3.0:
            s += 0.5 * weights.get("ma_converge", DEFAULT_WEIGHTS["ma_converge"])
        else:
            s -= 0.3 * weights.get("ma_converge", DEFAULT_WEIGHTS["ma_converge"])

    # ── 2026-08-11 R66: K 线形态打分 (5 维) ──

    # ma_align: 多头排列 1, 空头 -1, 中性 0
    ma_align = int(it.get("ma_align", 0) or 0)
    if ma_align != 0:
        s += ma_align * weights.get("ma_align", DEFAULT_WEIGHTS["ma_align"])

    # trend_5d 已在上面算过 (涨停前 5 日趋势)

    # vol_trend: 量能爆发 (1=中性, 2x=放量, 0.5x=缩量)
    vt = float(it.get("vol_trend", 0) or 0)
    if vt > 0:
        if vt >= 2.0:
            s += 1.0 * weights.get("vol_trend", DEFAULT_WEIGHTS["vol_trend"])
        elif vt >= 1.5:
            s += 0.5 * weights.get("vol_trend", DEFAULT_WEIGHTS["vol_trend"])
        elif vt < 0.5:
            s -= 0.5 * weights.get("vol_trend", DEFAULT_WEIGHTS["vol_trend"])

    # upper_shadow: 上影线 (高位抛压, 长上影减分)
    us = float(it.get("upper_shadow", 0) or 0)
    if us > 0:
        if us <= 1.0:  # 短上影 (T 字一字板)
            s += 0.3 * weights.get("upper_shadow", DEFAULT_WEIGHTS["upper_shadow"])
        elif us > 5.0:  # 长上影
            s -= 0.7 * weights.get("upper_shadow", DEFAULT_WEIGHTS["upper_shadow"])

    # body_at_pct: 实体长 → 强进攻信号
    ba = float(it.get("body_at_pct", 0) or 0)
    if ba > 0:
        if ba >= 8.0:
            s += 1.0 * weights.get("body_at_pct", DEFAULT_WEIGHTS["body_at_pct"])
        elif ba >= 5.0:
            s += 0.5 * weights.get("body_at_pct", DEFAULT_WEIGHTS["body_at_pct"])

    # ── 2026-08-11 R67: 技术指标 + 量价 + 时间效应 + 题材晋级 (12 维) ──

    # KDJ K 值: <20 超卖反弹 +0.7, 20-50 金叉 +0.5, >80 超买扣分
    kdj_v = float(it.get("kdj_k", 50.0) or 50.0)
    if kdj_v > 0:
        if kdj_v < 20:
            s += 0.7 * weights.get("kdj", DEFAULT_WEIGHTS["kdj"])
        elif kdj_v < 50:
            s += 0.5 * weights.get("kdj", DEFAULT_WEIGHTS["kdj"])
        elif kdj_v > 80:
            s -= 0.7 * weights.get("kdj", DEFAULT_WEIGHTS["kdj"])

    # RSI6: <30 超卖反弹 +, >70 超买 -, 50 中性
    rsi_v = float(it.get("rsi_6", 50.0) or 50.0)
    if rsi_v > 0:
        if rsi_v < 30:
            s += 0.7 * weights.get("rsi", DEFAULT_WEIGHTS["rsi"])
        elif rsi_v < 50:
            s += 0.3 * weights.get("rsi", DEFAULT_WEIGHTS["rsi"])
        elif rsi_v > 80:
            s -= 0.7 * weights.get("rsi", DEFAULT_WEIGHTS["rsi"])

    # BOLL: 上轨突破 +0.7, 中轨支撑 +0.4, 下轨跌破 -0.5
    bolu = float(it.get("boll_dist_upper", 0) or 0)
    boll = float(it.get("boll_dist_lower", 0) or 0)
    if bolu > 0 and bolu < 1.0:  # 突破上轨
        s += 0.7 * weights.get("boll", DEFAULT_WEIGHTS["boll"])
    elif boll > 0 and boll < 1.0:  # 下轨支撑反弹
        s += 0.4 * weights.get("boll", DEFAULT_WEIGHTS["boll"])
    if bolu > 5.0:  # 远离上轨=超买
        s -= 0.4 * weights.get("boll", DEFAULT_WEIGHTS["boll"])

    # MACD: DIF > 0 多头 +0.7, DIF 5d 转强 +0.4, 转弱 -0.4
    macd_v = float(it.get("macd_dif", 0) or 0)
    macd_chg = float(it.get("macd_dif_5d_chg", 0) or 0)
    if macd_v > 0:
        s += 0.7 * weights.get("macd", DEFAULT_WEIGHTS["macd"])
    elif macd_v < -0.1:
        s -= 0.5 * weights.get("macd", DEFAULT_WEIGHTS["macd"])
    if macd_chg > 0:
        s += 0.4 * weights.get("macd", DEFAULT_WEIGHTS["macd"])
    elif macd_chg < 0:
        s -= 0.4 * weights.get("macd", DEFAULT_WEIGHTS["macd"])

    # gap_jump: 跳空缺口 > 1% 加分
    gap_v = float(it.get("gap_pct", 0) or 0)
    if gap_v >= 1.0:
        s += 0.7 * weights.get("gap_jump", DEFAULT_WEIGHTS["gap_jump"])
    elif gap_v >= 0.5:
        s += 0.3 * weights.get("gap_jump", DEFAULT_WEIGHTS["gap_jump"])

    # yang_bao_yin: 长阳包阴 反包形态 +1.0
    yby = int(it.get("long_yang_bao_yin", 0) or 0)
    if yby:
        s += 1.0 * weights.get("yang_bao_yin", DEFAULT_WEIGHTS["yang_bao_yin"])

    # vp_dir: 量价同向天数 ≥ 3 加分 (齐升)
    vp_v = int(it.get("vp_same_dir_n", 0) or 0)
    if vp_v >= 3:
        s += 0.7 * weights.get("vp_dir", DEFAULT_WEIGHTS["vp_dir"])
    elif vp_v >= 2:
        s += 0.4 * weights.get("vp_dir", DEFAULT_WEIGHTS["vp_dir"])

    # vol_shrink_zt: 缩量涨停 = 筹码锁定 +0.7
    vs_v = int(it.get("vol_shrink_zt", 0) or 0)
    if vs_v:
        s += 0.7 * weights.get("vol_shrink_zt", DEFAULT_WEIGHTS["vol_shrink_zt"])

    # vol_top_div: 高位放量滞涨 → 减分
    vtd = int(it.get("vol_top_div", 0) or 0)
    if vtd:
        s -= 0.7 * weights.get("vol_top_div", DEFAULT_WEIGHTS["vol_top_div"])

    # vol_step: 量能台阶 ≥ 1.5 (逐步放量)
    vstep_v = float(it.get("vol_step_max", 0) or 0)
    if vstep_v >= 1.5:
        s += 0.7 * weights.get("vol_step", DEFAULT_WEIGHTS["vol_step"])
    elif vstep_v >= 1.2:
        s += 0.4 * weights.get("vol_step", DEFAULT_WEIGHTS["vol_step"])

    # weekday: 周一/周五效应 (回测有 weekday 字段)
    wd_v = int(it.get("weekday", -1) or -1)
    if wd_v >= 0:
        if wd_v == 0:  # 周一效应
            s += 0.5 * weights.get("weekday", DEFAULT_WEIGHTS["weekday"])
        elif wd_v == 4:  # 周五兑现
            s -= 0.3 * weights.get("weekday", DEFAULT_WEIGHTS["weekday"])

    # month_phase: 月末效应 (公募调仓窗口)
    mp_v = int(it.get("month_phase", 0) or 0)
    if mp_v == 1:  # 月末
        s += 0.5 * weights.get("month_phase", DEFAULT_WEIGHTS["month_phase"])
    elif mp_v == -1:  # 月初
        s -= 0.3 * weights.get("month_phase", DEFAULT_WEIGHTS["month_phase"])

    # report_window: 季报窗口避雷 (-0.5)
    rw_v = int(it.get("report_window", 0) or 0)
    if rw_v:
        s -= 0.5 * weights.get("report_window", DEFAULT_WEIGHTS["report_window"])

    # promote_ratio: 题材晋级率 (高 = 题材活跃, 持续性强)
    pr_v = float(it.get("promote_ratio", 0) or 0)
    if pr_v > 0:
        if pr_v >= 30.0:  # 高晋级
            s += 1.0 * weights.get("promote_ratio", DEFAULT_WEIGHTS["promote_ratio"])
        elif pr_v >= 15.0:
            s += 0.5 * weights.get("promote_ratio", DEFAULT_WEIGHTS["promote_ratio"])
        elif pr_v < 5.0:
            s -= 0.4 * weights.get("promote_ratio", DEFAULT_WEIGHTS["promote_ratio"])

    # prev_zt_perf: 昨日涨停均收益 (> 0 = 题材溢价强)
    pz_v = float(it.get("prev_zt_avg_ret", 0) or 0)
    if pz_v != 0:
        if pz_v >= 2.0:  # 强溢价
            s += 1.0 * weights.get("prev_zt_perf", DEFAULT_WEIGHTS["prev_zt_perf"])
        elif pz_v > 0:
            s += 0.5 * weights.get("prev_zt_perf", DEFAULT_WEIGHTS["prev_zt_perf"])
        elif pz_v <= -2.0:  # 强杀溢价
            s -= 0.7 * weights.get("prev_zt_perf", DEFAULT_WEIGHTS["prev_zt_perf"])

    # strong_zt_ratio: 强势涨停占比 (strength signal)
    szr_v = float(it.get("strong_zt_ratio", 0) or 0)
    if szr_v > 0:
        if szr_v >= 0.4:
            s += 0.7 * weights.get("strong_zt", DEFAULT_WEIGHTS["strong_zt"])
        elif szr_v >= 0.2:
            s += 0.4 * weights.get("strong_zt", DEFAULT_WEIGHTS["strong_zt"])
        elif szr_v < 0.1:
            s -= 0.3 * weights.get("strong_zt", DEFAULT_WEIGHTS["strong_zt"])

    return round(s, 2)


def rating_of(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B+"
    if score >= 70:
        return "B"
    return "C"
