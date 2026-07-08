"""
tuixue_v3/layer2_cycle_mainline.py
Layer 2：市场情绪周期 + 主线题材 + 盈亏比前置过滤
- 周期闸门：仅 启动 / 确认 阶段允许开仓
- 主线识别：板块当日上涨 ≥ 40 + 资金净流入前三 + 涨跌比例 ≥ 2:1
- 盈亏比：≥ 2.5:1
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import config as cfg
from . import data_layer as dl
from . import lib_common as lc
from . import multi_source_fetchers as msf

log = logging.getLogger("tuixue_v3.layer2")


# ═══════════════════════════════════════════════════
# 周期闸门
# ═══════════════════════════════════════════════════
def cycle_gate(date_str: str | None = None, calendar: dict | None = None) -> tuple[str, dict]:
    """
    返回 (reason, detail)
    reason: "allow" / "block"
    detail: {phase, score, zt_count, ...}
    calendar: backtest 模式传入的预加载字典 {date_str: {phase, score, ...}}

    数据源（3 种逃生）:
      1) emotion.compute_daily_emotion — 主源
      2) akshare 实时拉涨停 / 跌停池估算 phase — 备 1
      3) 默认 allow（带 caveat，不允许情绪冰点） — 兜底
    """
    # 优先用 calendar（回测模式）
    if calendar is not None and date_str and date_str in calendar:
        emo = calendar[date_str]
    else:
        emo: dict = {}

        # ─── 主源 1: emotion 模块 ───
        try:
            from emotion import compute_daily_emotion
            emo = compute_daily_emotion(date_str) or {}
        except Exception as e:
            log.warning(f"emotion 拉取失败: {e}")
            emo = {}

        # ─── 备 1: akshare 实时推算 phase ───
        if not emo:
            try:
                from . import multi_source_fetchers as msf
                # fetch_zt_pool 需要 YYYYMMDD；没传就用今天
                today_str = date_str or datetime.now().strftime("%Y%m%d")
                zt_pool = msf.fetch_zt_pool(today_str) or []
                zt_count = len(zt_pool) if isinstance(zt_pool, list) else 0
                # 阶段粗略判断（不精准但能跑）
                # 数据源不可达时 zt_count 会是 0 — 但 0 不一定=冰点（可能是网络问题），给中性
                if zt_count >= 50:
                    phase = "高潮"
                    score = 80.0
                elif zt_count >= 25:
                    phase = "确认"
                    score = 60.0
                elif zt_count >= 10:
                    phase = "启动"
                    score = 45.0
                elif zt_count >= 1:
                    phase = "修复"
                    score = 30.0
                else:
                    # zt=0 不要直接判冰点 — 可能是数据源不可达，给中性"确认"让 L2 不 block
                    phase = "确认"
                    score = 50.0
                emo = {
                    "phase": phase,
                    "emotion_score": score,
                    "zt_count": zt_count,
                    "max_cb": 0,
                    "components": {},
                    "source": "akshare_fallback",
                    "fallback_warning": "emotion 模块不可用,使用 akshare 推算" if zt_count > 0 else "涨停池数据为 0,使用中性默认(数据源可能不可达)",
                }
                log.info(f"emotion 主源不可用，akshare 兜底: phase={phase} zt={zt_count}")
            except Exception as e:
                log.warning(f"akshare 兜底也失败: {e}")
                # ─── 兜底 3: allow + caveat ───
                emo = {
                    "phase": "确认",
                    "emotion_score": 50.0,
                    "zt_count": 0,
                    "max_cb": 0,
                    "components": {},
                    "source": "neutral_default",
                    "fallback_warning": "情绪数据全部不可用，采用中性默认",
                }

    phase = emo.get("phase", "冰点")
    score = float(emo.get("emotion_score", 0))
    zt_count = int(emo.get("zt_count", 0))

    detail = {
        "phase": phase,
        "emotion_score": score,
        "zt_count": zt_count,
        "max_streak": emo.get("max_cb", 0),
        "components": emo.get("components", {}),
        "source": emo.get("source", "live"),
    }

    if phase in cfg.L2_PHASE_ALLOW:
        return "allow", detail

    if phase == "冰点":
        return "block", {**detail, "block_reason": "冰点周期直接屏蔽（情绪未释放充分）"}

    return "block", {**detail, "block_reason": f"阶段={phase} 不在开仓白名单 {cfg.L2_PHASE_ALLOW}"}


# ═══════════════════════════════════════════════════
# 主线板块识别
# ═══════════════════════════════════════════════════
def identify_mainline(date_str: str | None = None, calendar: dict | None = None) -> list[dict]:
    """
    返回主线板块列表：[{"name": ..., "code": ..., "rise_count": ..., "fund_flow_yi": ...}, ...]
    calendar: backtest 模式传入的预加载 {date_str: set_of_sector_names}
    """
    # 回测模式：calendar 直接给主线板块名集合
    if calendar is not None and date_str and date_str in calendar:
        names = calendar[date_str]
        return [{"name": n, "code": "", "rise_count": 0, "fund_flow_yi": 0, "ratio": 0,
                 "source": "calendar"} for n in names][:cfg.L2_MAINLINE_FUND_FLOW_TOPN]

    cache_key = f"mainline_{date_str or 'latest'}"
    cached = dl._cache_load(cache_key, cfg.CACHE_TTL_FUNDAMENTAL)
    if cached:
        return cached

    sectors: list = []
    # ─── 主源 1: multi_source_fetchers 内部接口 ───
    try:
        sectors = msf._em_sectors_realtime(top_n=30) if hasattr(msf, "_em_sectors_realtime") else []
    except Exception:
        sectors = []

    # ─── 备 1: emotion 模块 ───
    if not sectors:
        try:
            from emotion import _em_sectors_realtime as _em
            sectors = _em(top_n=30) or []
        except Exception as e:
            log.warning(f"emotion._em_sectors_realtime 失败: {e}")

    # ─── 兜底 2: THS 同花顺板块（stock_board_industry_summary_ths 单接口含 涨跌幅/净流入/上涨家数/下跌家数） ───
    # 8s 硬超时：上游 hang 时不拖死整条 screen（之前 pool=50 卡 60s 就是这里）
    if not sectors:
        def _fetch_hot():
            import akshare as ak
            df = ak.stock_board_industry_summary_ths()
            if df is None or df.empty:
                return []
            try:
                name_df = ak.stock_board_industry_name_ths()
                name_to_code = {str(r.get("name", "")): str(r.get("code", ""))
                                for _, r in name_df.iterrows()}
            except Exception:
                name_to_code = {}
            rows = []
            for _, r in df.iterrows():
                name = str(r.get("板块", ""))
                rows.append({
                    "板块名称": name,
                    "板块代码": name_to_code.get(name, name),
                    "涨家数": int(r.get("上涨家数", 0) or 0),
                    "跌家数": int(r.get("下跌家数", 0) or 0),
                    "主力净流入": float(r.get("净流入", 0) or 0) * 1e8,  # 亿 → 元
                    "涨跌幅": float(r.get("涨跌幅", 0) or 0),
                })
            return rows
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
        with ThreadPoolExecutor(max_workers=1) as ex:
            try:
                sectors = ex.submit(_fetch_hot).result(timeout=8)
            except FutTimeout:
                log.warning("THS 板块兜底超时 8s,放弃主线识别")
                return []
            except Exception as e:
                log.warning(f"THS 板块兜底失败: {e}")
                return []

    # ─── 老 akshare 东财接口（2026-07 持续 RemoteDisconnected,已不推荐,保留兼容） ───
    # 2026-07: 用 ThreadPoolExecutor 兜底超时,即使挂死也不拖垮 screen
    if not sectors:
        def _ak_legacy():
            import akshare as ak
            df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
            if df is None or df.empty:
                return []
            rows = []
            for _, r in df.head(30).iterrows():
                rows.append({
                    "板块名称": str(r.get("名称", "")),
                    "板块代码": str(r.get("名称", "")),
                    "涨家数": 0, "跌家数": 0,
                    "主力净流入": float(r.get("主力净流入-净额", 0) or 0),
                })
            return rows
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
        with ThreadPoolExecutor(max_workers=1) as ex:
            try:
                sectors = ex.submit(_ak_legacy).result(timeout=5)
            except FutTimeout:
                log.warning("ak.stock_sector_fund_flow_rank 超时 5s,东财接口挂死")
                sectors = []
            except Exception as e:
                log.warning(f"akshare 板块兜底也失败: {e}")
                sectors = []
        if not sectors:
            return []  # 真没数据，让上层决策

    mainline = []
    for s in sectors[:10]:
        rise_count = int(s.get("rise_count", s.get("涨家数", 0)) or 0)
        fall_count = int(s.get("fall_count", s.get("跌家数", 0)) or 0)
        fund_flow = float(s.get("主力净流入", s.get("main_net_inflow", 0)) or 0)
        ratio = rise_count / max(fall_count, 1)
        if rise_count >= cfg.L2_MAINLINE_RISE_MIN and ratio >= cfg.L2_MAINLINE_RATIO_MIN:
            mainline.append({
                "name": s.get("板块名称", s.get("name", "")),
                "code": s.get("板块代码", s.get("code", "")),
                "rise_count": rise_count,
                "fall_count": fall_count,
                "fund_flow_yi": fund_flow / 1e8,
                "ratio": round(ratio, 2),
            })

    mainline.sort(key=lambda x: (x["fund_flow_yi"], x["rise_count"]), reverse=True)
    mainline = mainline[:cfg.L2_MAINLINE_FUND_FLOW_TOPN]

    dl._cache_save(cache_key, mainline)
    return mainline


def get_mainline_constituents(mainline: list[dict]) -> dict[str, set]:
    """返回 {板块名: {个股代码, ...}}。2026-07: 并行 + 6s 总硬超时,东财 cons 接口挂死不拖死 screen"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    out: dict[str, set] = {s.get("name", ""): set() for s in mainline}
    if not mainline:
        return out

    def _fetch_one(s):
        name, code = s.get("name", ""), s.get("code", "")
        if not code:
            return name, set()
        try:
            cons = msf.fetch_sector_constituents(code, name)
            return name, {c for c, _ in cons}
        except Exception as e:
            log.warning(f"拉板块 {name} 成份股失败: {e}")
            return name, set()

    with ThreadPoolExecutor(max_workers=min(4, len(mainline))) as ex:
        futures = {ex.submit(_fetch_one, s): s for s in mainline}
        try:
            for fut in as_completed(futures, timeout=6):
                name, codes = fut.result(timeout=1)
                out[name] = codes
        except Exception:
            # 兜底:超过 6s 还没拿完就放弃剩下的
            log.warning(f"get_mainline_constituents 部分超时,已收 {len([v for v in out.values() if v])} / {len(mainline)}")
    return out


# ═══════════════════════════════════════════════════
# 盈亏比前置过滤（候选个股必须 ≥ 2.5:1）
# ═══════════════════════════════════════════════════
def calc_rr_ratio(df: pd.DataFrame) -> tuple[float, dict]:
    """
    估算候选个股理论盈亏比：
      - Reward（止盈目标）：基础 6%（移动止盈触发位），若 ATR 显著放大则上沿至 ATR × 2（max 15%）
      - Risk（止损位）：min(MA10 距离, 5%) —— 取较小者更激进（贴合"打板 2% / MA10 清仓"两条规则）
      - 风险下限 2%（防止距离 MA10 太近 → RR 虚高）
      - RR = reward / risk

    返回 (rr_ratio, detail)
    """
    if df is None or len(df) < 25:
        return 0.0, {"reason": "数据不足"}

    last = df.iloc[-1]
    price = float(last["收盘"])

    # 风险位
    if "MA10" in df.columns and pd.notna(df["MA10"].iloc[-1]):
        ma10_dist = abs(price - float(df["MA10"].iloc[-1])) / price * 100
    else:
        ma10_dist = 3.0
    risk_pct = max(min(ma10_dist, 5.0), 2.0)  # 2% ~ 5%

    # 收益位
    reward_pct = 6.0
    if "最高" in df.columns and "最低" in df.columns:
        tr = (df["最高"] - df["最低"]) / df["收盘"]
        atr_pct = float(tr.tail(20).mean() * 100)
        if atr_pct > 4.0:
            reward_pct = min(max(atr_pct * 2.0, 6.0), 15.0)

    rr = reward_pct / risk_pct if risk_pct > 0 else 0
    return round(rr, 2), {
        "reward_pct": round(reward_pct, 2),
        "risk_pct": round(risk_pct, 2),
        "rr": rr,
        "price": price,
    }


# ═══════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════
def screen(stocks: list[dict], date_str: str | None = None,
           emotion_cal: dict | None = None, mainline_cal: dict | None = None) -> tuple[list[dict], dict]:
    """
    输入：Layer1 输出（含 _df_ref）
    输出：(幸存list[dict], 统计dict)
    emotion_cal / mainline_cal: 回测模式传入日历
    """
    stats = {
        "input": len(stocks),
        "cycle_blocked": 0,
        "no_mainline": 0,
        "not_in_mainline": 0,
        "rr_too_low": 0,
        "passed": 0,
    }

    # 1) 周期闸门
    cycle_res, cycle_detail = cycle_gate(date_str, calendar=emotion_cal)
    if cycle_res != "allow":
        stats["cycle_blocked"] = len(stocks)
        stats["cycle_detail"] = cycle_detail
        log.info(f"Layer2 闸门阻断: {cycle_detail}")
        return [], stats

    # 2) 主线识别
    mainline = identify_mainline(date_str, calendar=mainline_cal)
    if not mainline and mainline_cal is None:
        # 实盘模式无主线 + 上游全断 → 降级：放行所有 L1 幸存做 RR 过滤（铁律三.5 空仓仍是底线，但 cycle 已 allow 才走这步）
        stats["no_mainline"] = len(stocks)
        stats["mainline_degraded"] = True
        stats["mainline_degraded_reason"] = "上游数据源全部不可达，跳过主线板块匹配，仅做 RR 过滤"
        log.warning("Layer2 主线板块空 · 上游限频/断网 · 降级放行所有 L1 幸存做 RR 过滤")
        mainline = [{"name": "(数据源不可达,主线降级)", "code": "", "source": "mainline_fallback"}]
        # 让 cons_map 放行所有
        cons_map = {"(数据源不可达,主线降级)": set()}  # 空集合 → 不过滤任何股

    # 回测模式主线为空 / 失败：放行所有 L1 幸存做 RR 过滤（保守）
    if not mainline and mainline_cal is not None:
        log.info("Layer2 回测模式主线空 → 放行所有 L1 幸存做 RR 过滤")
        mainline = [{"name": "(回测无主线)", "code": "", "source": "fallback"}]

    cons_map = get_mainline_constituents(mainline)
    if not any(cons_map.values()):
        # ─── 2026-07 软匹配兜底 ───
        # 东财 cons 接口挂死时,cons_map 全空,但 pre_meta 已注入 recent_hot_sector_name
        # 用 mainline 板块名 vs stock.recent_hot_sector_name 双向 substring 匹配
        mainline_names = [m.get("name", "") for m in mainline if m.get("name")]
        if mainline_names and any(s.get("recent_hot_sector_name") for s in stocks):
            log.info("cons_map 全空 · 改用 recent_hot_sector_name 软匹配")
            for s in stocks:
                hs_name = s.get("recent_hot_sector_name", "")
                if not hs_name:
                    continue
                for mn in mainline_names:
                    if (mn and (mn in hs_name or hs_name in mn)):
                        cons_map[mn] = cons_map.get(mn, set()) | {s["code"]}
                        break

    if not any(cons_map.values()):
        # 降级模式（数据源不可达）或回测模式无成份股：放行所有 L1 幸存做 RR 过滤
        if mainline_cal is not None or stats.get("mainline_degraded"):
            label = "降级" if stats.get("mainline_degraded") else "回测"
            # 降级模式放宽 RR 阈值（大盘股 RR 自然偏低）
            rr_min = 1.5 if stats.get("mainline_degraded") else cfg.L2_RR_RATIO_MIN
            log.info(f"Layer2 {label}模式主线无成份股 → 放行所有 L1 幸存做 RR 过滤 (RR≥{rr_min})")
            passed = []
            for s in stocks:
                df = s.get("_df_ref")
                rr, rr_detail = calc_rr_ratio(df)
                if rr < rr_min:
                    stats["rr_too_low"] += 1
                    continue
                s["sector"] = "(主线板块)"
                s["rr_ratio"] = rr
                s["rr_detail"] = rr_detail
                s["pass_detail"] = s.get("pass_detail", "") + f" | L2: {label} RR={rr}"
                passed.append(s)
            stats["passed"] = len(passed)
            stats["mainline"] = mainline
            stats["cycle_detail"] = cycle_detail
            return passed, stats
        stats["no_mainline"] = len(stocks)
        return [], stats

    code_to_sector: dict[str, str] = {}
    for sector_name, codes in cons_map.items():
        for c in codes:
            code_to_sector[c] = sector_name

    # 3) 主线归属 + 盈亏比过滤
    passed = []
    for s in stocks:
        code = s["code"]
        if code not in code_to_sector:
            stats["not_in_mainline"] += 1
            continue

        df = s.get("_df_ref")
        rr, rr_detail = calc_rr_ratio(df)
        if rr < cfg.L2_RR_RATIO_MIN:
            stats["rr_too_low"] += 1
            continue

        s["sector"] = code_to_sector[code]
        s["rr_ratio"] = rr
        s["rr_detail"] = rr_detail
        s["pass_detail"] = s.get("pass_detail", "") + f" | L2: 主线={code_to_sector[code]} RR={rr}"
        passed.append(s)

    stats["passed"] = len(passed)
    stats["mainline"] = mainline
    stats["cycle_detail"] = cycle_detail
    log.info(f"Layer2: {stats}")
    return passed, stats