#!/usr/bin/env python3
"""
R67 · 野人哥战法多维度回测
════════════════════════════
对历史每个交易日,跑 17 条战法 + 5 个套餐,统计胜率(T+1/T+3/T+5)。

维度扩展(在 dragons 已有 21 字段基础上叠加):
  日/周/月 K线: MA5/10/20/60, 高低/均价/振幅/换手/量比, MACD, N字
  资金: 主力净流、龙虎榜席位、行业板块涨跌幅
  题材: 板块热度/涨停数  -- v2
  尾盘 30min: tail_pct_vol, last_tail_drop_pct (Y13)

退场模型:
  T+1: 次日 close ≥ entry×1.02 → WIN; ≤ entry×0.95 → LOSS; 其他 NEUTRAL
  T+3: 三日内最高 ≥ entry×1.05 → WIN
  T+5: 五日内最高 ≥ entry×1.08 → WIN
  推进策略: 每日首盘 K 线就买,省去印花税/手续费 (回测假设)
"""
from __future__ import annotations
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from tuixue_v3 import yeren_laws as _yl
from tuixue_v3.multi_source_fetchers import (
    fetch_zt_pool, fetch_kline_em_period, fetch_weekly_em, fetch_monthly_em,
    fetch_tail_30min, fetch_trade_dates, fetch_finance_growth,
)

TECH_SECTORS = {
    "半导体","PCB","电子","元件","消费电子","光学","光学光电子","通信","通信设备",
    "计算机","软件","互联网","传媒","游戏","AI","算力","CPO","国产芯片","数据要素",
    "数字货币","机器人","智能穿戴","汽车电子","电池","新能源","光伏","储能","医药",
    "生物","医疗服务","AR","VR","智能眼镜","显示器","面板","电源设备","自动化设备",
}

# ─── 维度计算 (日 K,快路径) ─────────────────────
_DIMS_CACHE: dict[tuple[str, str], dict] = {}
_FINANCE_CACHE: dict[str, dict] = {}  # 业绩同比 (跨日期共享)
_FINANCE_REDIS_TTL = 86400  # 业绩季度数据, 24h 缓存足够


def _finance_redis_get(code: str):
    try:
        from tuixue_v3.cache_store import get_store
        r = get_store()._redis
        if r is None:
            return None
        raw = r.get(f"yeren:fin:{code}")
        if raw:
            import json as _j
            return _j.loads(raw)
    except Exception:
        return None
    return None


def _finance_redis_set(code: str, val: dict | None) -> None:
    try:
        from tuixue_v3.cache_store import get_store
        r = get_store()._redis
        if r is None:
            return
        import json as _j
        r.setex(f"yeren:fin:{code}", _FINANCE_REDIS_TTL, _j.dumps(val, default=str))
    except Exception:
        pass


def _parse_kline_line(line: str) -> dict | None:
    parts = line.split(",")
    if len(parts) < 11:
        return None
    try:
        return {
            "date": parts[0],
            "open": float(parts[1]),
            "close": float(parts[2]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            "vol": float(parts[5]),
            "amount": float(parts[6]),
            "amp_pct": float(parts[7]),
            "change_pct": float(parts[8]),
            "turnover_pct": float(parts[10]),
        }
    except Exception:
        return None


_DIMS_REDIS_TTL = 3600  # 1 hour; entry_date 早于今日则内容固定


def _dims_redis_get(key: tuple[str, str]):
    try:
        from tuixue_v3.cache_store import get_store
        r = get_store()._redis
        if r is None:
            return None
        raw = r.get(f"yeren:dims:{key[0]}:{key[1]}")
        if raw:
            import json as _j
            return _j.loads(raw)
    except Exception:
        return None
    return None


def _dims_redis_set(key: tuple[str, str], val: dict) -> None:
    try:
        from tuixue_v3.cache_store import get_store
        r = get_store()._redis
        if r is None:
            return
        import json as _j
        r.setex(f"yeren:dims:{key[0]}:{key[1]}", _DIMS_REDIS_TTL, _j.dumps(val, default=str))
    except Exception:
        pass


def _compute_kline_dims_for_date(code: str, entry_date: str) -> dict:
    """单只股票在 entry_date 当天的 K 线维度 (跑一次, 后用于所有规则)。

    输入: code, entry_date YYYYMMDD
    输出: dict 含 MA5/10/20/60, MACD, N字, 周月趋势等
    R-fix-2026-08-15: 加 Redis 缓存层 — 之前纯 in-process dict,
    多 worker + 重启后都重算, /api/yeren/realtime 50+ stocks × 3 fetches 起步
    """
    key = (code, entry_date)
    if key in _DIMS_CACHE:
        return _DIMS_CACHE[key]  # entry_date 早于最新, kline 内容固定
    cached = _dims_redis_get(key)
    if cached:
        _DIMS_CACHE[key] = cached
        return cached

    out = {"code": code, "entry_date": entry_date}
    try:
        lines = fetch_kline_em_period(code, klt=101, lmt=200, fqt=1, end="20500101") or []
        daily = [d for d in (_parse_kline_line(l) for l in lines) if d]
        daily = [d for d in daily if d["date"].replace("-", "") <= entry_date]
        if len(daily) < 5:
            return out
        idx = len(daily) - 1
        entry = daily[idx]
        out["close"] = entry["close"]
        for n in (5, 10, 20, 60):
            win = daily[max(0, idx-n+1): idx+1]
            out[f"ma{n}"] = sum(d["close"] for d in win) / len(win)
        out["vol_ratio_5d"] = entry["vol"] / (sum(d["vol"] for d in daily[max(0, idx-5+1): idx+1]) / 5 or 1)
        # MACD 简化
        if idx >= 25:
            e12 = sum(d["close"] for d in daily[idx-12+1: idx+1]) / 12
            e26 = sum(d["close"] for d in daily[idx-26+1: idx+1]) / 26
            out["macd"] = round(e12 - e26, 3)
        # N字 (近 10 日)
        if idx >= 10:
            win10 = daily[idx-10: idx+1]
            mid_idx = min(range(len(win10)), key=lambda i: win10[i]["low"])
            if 3 <= mid_idx <= 7:
                left_low = min(d["low"] for d in win10[:mid_idx])
                right_low = min(d["low"] for d in win10[mid_idx+1:])
                out["n_shape_ok"] = right_low >= left_low * 0.99
        # 周 K 近 5 周趋势
        try:
            wk = [d for d in (_parse_kline_line(l) for l in (fetch_weekly_em(code, 12) or [])) if d]
            wk = [d for d in wk if d["date"].replace("-", "") <= entry_date]
            if len(wk) >= 5:
                wk5_close = sum(d["close"] for d in wk[-5:]) / 5
                out["wk_trend_up"] = wk[-1]["close"] > wk5_close
        except Exception:
            pass
        # 月 K 近 3 月趋势
        try:
            mk = [d for d in (_parse_kline_line(l) for l in (fetch_monthly_em(code, 12) or [])) if d]
            mk = [d for d in mk if d["date"].replace("-", "") <= entry_date]
            if len(mk) >= 3:
                out["monthly_trend_up"] = mk[-1]["close"] > (sum(d["close"] for d in mk[-3:]) / 3)
        except Exception:
            pass
    except Exception as e:
        out["_err"] = str(e)
    _DIMS_CACHE[key] = out
    _dims_redis_set(key, out)
    return out


# ─── 完整 K 线 (含未来,用于算 T+N 胜率) ─────────
_KLINE_CACHE: dict[str, list[dict]] = {}
_KLINE_LOCKS: dict[str, object] = {}


def _fetch_full_kline(code: str, start_date: str, end_date: str) -> list[dict]:
    """拉 code 从 start_date 到 end_date 的日 K (用于算 T+5 等)。
    进程内 LRU cache (避免跨 days/combos 重复拉)。
    """
    import threading as _th
    if code not in _KLINE_LOCKS:
        _KLINE_LOCKS[code] = _th.Lock()
    with _KLINE_LOCKS[code]:
        if code not in _KLINE_CACHE:
            try:
                lines = fetch_kline_em_period(code, klt=101, lmt=400, fqt=1, end="20500101") or []
                daily = [d for d in (_parse_kline_line(l) for l in lines) if d]
                _KLINE_CACHE[code] = daily
            except Exception:
                _KLINE_CACHE[code] = []
    full = _KLINE_CACHE[code]
    return [d for d in full if start_date <= d["date"].replace("-", "") <= end_date]


def _calc_winrate(klines: list[dict], entry_idx: int, entry_close: float) -> dict:
    """给定 K 线 + entry_index, 算 T+1/T+3/T+5 收益。

    R71 · WIN 口径改为"区间最低 ≤ +0% (未破位) 且 期末 ≥ entry×(1+win_pct)"。
    真实交易者的胜率: **次日能卖在不亏 + 赚 ≥ 1%**, 不是"日内瞬间 +2%"。
    这才是"账户真实赚钱"的口径。

    详细判定:
      WIN     = 期末 close ≥ entry × 1.01  (赚 ≥ 1%) 且 区间最低 > entry × 0.97 (没破位)
      LOSS    = 期末 close ≤ entry × 0.97 (亏 ≥ 3%) OR 区间最低 ≤ entry × 0.95 (止损)
      HOLD    = 期末在 (-1%, +1%) 之间
      FLAT    = 其他
    """
    out = {}
    for label, days in [
        ("T+1", 1),
        ("T+3", 3),
        ("T+5", 5),
    ]:
        nxt_idx = entry_idx + days
        if entry_idx is None or entry_idx >= len(klines) - 1:
            out[label] = None
            continue
        segment = klines[entry_idx: nxt_idx+1]
        if not segment:
            out[label] = None
            continue
        high = max(d["high"] for d in segment[1:]) if len(segment) > 1 else segment[0]["high"]
        low = min(d["low"] for d in segment[1:]) if len(segment) > 1 else segment[0]["low"]
        last = segment[-1]
        if entry_close <= 0:
            out[label] = None
            continue
        high_pct = (high - entry_close) / entry_close
        low_pct = (low - entry_close) / entry_close
        last_pct = (last["close"] - entry_close) / entry_close
        # R71 · 新口径
        if last_pct >= 0.01 and low_pct > -0.03:
            out[label] = "WIN"
        elif last_pct <= -0.03 or low_pct <= -0.05:
            out[label] = "LOSS"
        elif last_pct >= 0.005:
            out[label] = "HOLD-WIN"
        elif last_pct <= -0.01:
            out[label] = "HOLD-LOSS"
        else:
            out[label] = "FLAT"
        # 同步记录: 实际收益(用于期望值)
        out[f"{label}_pct"] = round(last_pct, 4)
        out[f"{label}_max"] = round(high_pct, 4)
        out[f"{label}_min"] = round(low_pct, 4)
    return out


# ─── 17 规则评估 ──────────────────────────────
def _rule_eval(rid: str, c: dict, dims: dict, tail30: dict | None) -> dict:
    name = c.get("name", "")
    sector = c.get("sector", "")
    l1 = (c.get("taxonomy") or {}).get("l1", "")
    l2 = (c.get("taxonomy") or {}).get("l2", "")
    streak = c.get("streak", 0) or 0
    seal = c.get("seal_ratio_pct", 0) or 0
    turnover = c.get("turnover_pct", 0) or 0
    mcap = c.get("market_cap_yi", 0) or 0
    is_mainline = c.get("is_mainline", False)
    first_time = c.get("first_time", "") or ""
    burst = c.get("burst_count", 0) or 0
    seats = c.get("seat_aliases", []) or []
    sr_pass = seal > 30 if seal <= 100 else seal > 300

    if rid == "Y01":
        ok = streak in (1, 2) and sr_pass and not any("拉萨" in s for s in seats)
        return {"passed": ok, "weight": 1.0, "note": f"N字{streak}板+封{seal}+封成"}
    if rid == "Y02":
        es = False
        ft = first_time.strip() if first_time else ""
        # AKShare first_time = "HHMMSS" (6 chars) or "HH:MM:SS" (8 chars) or "HHMM" (4 chars)
        digits = "".join(c for c in ft if c.isdigit())
        if len(digits) >= 4:
            hh = int(digits[:2]); mm = int(digits[2:4])
            es = (hh < 14) or (hh == 14 and mm < 30)
        ok = sr_pass and burst == 0 and es
        return {"passed": ok, "weight": 1.0, "note": f"封成{seal}+首时{first_time}+炸{burst}"}
    if rid == "Y03":
        ok = streak == 3 and 8 <= turnover <= 25
        return {"passed": ok, "weight": 0.9, "note": f"3板换手{turnover}%∈[8,25]"}
    if rid == "Y04":
        # 反向规则
        return {"passed": not (streak >= 4 and turnover < 3), "weight": -0.7, "note": f"{streak}板换手{turnover}%"}
    if rid == "Y05":
        ok = 30 <= mcap <= 150 and 5 <= turnover <= 30 and is_mainline
        return {"passed": ok, "weight": 0.7, "note": f"市值{mcap}+换手{turnover}+主线{is_mainline}"}
    if rid == "Y06":
        return {"passed": True, "weight": 1.0, "note": "纪律:回撤≥8%止损"}
    if rid == "Y07":
        es = False
        ft = first_time.strip() if first_time else ""
        digits = "".join(c for c in ft if c.isdigit())
        if len(digits) >= 4:
            hh = int(digits[:2]); mm = int(digits[2:4])
            es = (hh < 14) or (hh == 14 and mm < 30)
        return {"passed": es, "weight": 0.9, "note": f"首时{first_time}(<14:30=买点)"}
    if rid == "Y08":
        in_tech = any(t in (sector + l1 + l2) for t in TECH_SECTORS)
        n = dims.get("n_shape_ok") if isinstance(dims.get("n_shape_ok"), bool) else False
        return {"passed": in_tech, "weight": 0.8, "note": f"科技={in_tech}+N字={n}"}
    if rid == "Y09":
        in_tech = any(t in (sector + l1 + l2) for t in TECH_SECTORS)
        ok = in_tech and is_mainline
        return {"passed": ok, "weight": 0.9, "note": f"科技={in_tech}+主线={is_mainline}"}
    if rid == "Y10":
        ok = any(k in (sector + l1 + l2) for k in ("PCB","半导体","元件","电子","国产"))
        return {"passed": ok, "weight": 0.6, "note": f"PCB/国产={ok}板块[{sector}/{l1}/{l2}]"}
    if rid == "Y11":
        # R70 · 业绩反转 (YOY 拐点 OR 周期景气)
        fin_yoy = c.get("fin_latest_yoy")
        turn = c.get("fin_turn_point")
        if fin_yoy is not None and fin_yoy > 20:
            ok = True
            note = f"业绩高增 yoy={fin_yoy:.0f}%"
        elif turn:
            ok = True
            note = f"业绩反转 turn_point yoy={fin_yoy:.0f}%"
        else:
            ok = any(k in (sector + l1 + l2) for k in ("化工", "医药", "新能源", "光伏", "锂", "稀土", "煤炭", "钢铁"))
            note = f"周期景气(启发式){ok}"
        return {"passed": ok, "weight": 0.7, "note": note}
    if rid == "Y12":
        ok = any(k in (sector + l1 + l2) for k in ("AR","VR","消费电子","光学","智能眼镜","智能穿戴"))
        return {"passed": ok, "weight": 0.5, "note": f"AR/VR/消费电子={ok}"}
    if rid == "Y13":
        # 尾盘套利: 尾盘放量 + 收阴 + 题材未死
        if not tail30:
            return {"passed": False, "weight": 0.5, "note": "尾盘数据缺"}
        ok = (tail30.get("tail_pct_vol", 0) > 0.25
              and tail30.get("last_change_pct", 0) < 0
              and tail30.get("close_pos_in_range", 1) < 0.3)
        return {"passed": ok, "weight": 0.4, "note": f"尾盘量{tail30.get('tail_pct_vol', 0)*100:.0f}%+收阴{ok}"}
    if rid == "Y14":
        return {"passed": True, "weight": 1.0, "note": "纪律:固定模式"}
    if rid == "Y15":
        is_leader = streak >= 3 and is_mainline and sr_pass
        return {"passed": is_leader, "weight": 1.0, "note": f"四问:主线{is_mainline}+龙头{streak}板+封{seal}"}
    if rid == "Y16":
        # 估值有空间
        pe = c.get("pe_ttm", 0) or 0
        ok = 0 < pe < 50
        return {"passed": ok, "weight": 0.5, "note": f"PE-TTM={pe}(0,50)"}
    if rid == "Y17":
        return {"passed": not any("拉萨" in s for s in seats), "weight": -0.8,
                "note": f"拉萨过滤={seats[:2]}"}
    return {"passed": False, "weight": 0.0, "note": f"unknown {rid}"}


def _enrich_zt_pool(pool: list[dict], hot_sectors: list[dict], sector_taxonomy: dict | None = None) -> list[dict]:
    """为裸 zt_pool 加上 dragons 关键维度。
    seal_ratio_pct = limit_order_amount / amount * 100 (基于 AKShare 字段)
    is_mainline = sector ⊂ hot_sectors OR sector ∈ TECH_SECTORS (泛科技/景气) 视为主线启发
    """
    mainline_names = [s.get("name", "") for s in hot_sectors[:10] if s.get("name")]
    try:
        from tuixue_v3.web.sector_taxonomy import classify_sector_name
    except Exception:
        classify_sector_name = lambda s: {"l1": "", "l2": ""}
    for c in pool:
        c["limit_order_amount"] = float(c.get("limit_order_amount", 0) or 0)
        c["amount"] = float(c.get("amount", 0) or 0)
        if c["amount"] > 0 and c["limit_order_amount"] > 0:
            c["seal_ratio_pct"] = round(c["limit_order_amount"] / c["amount"] * 100, 1)
        else:
            c["seal_ratio_pct"] = 0.0
        sector = c.get("sector", "")
        # 主线启发: hot_sectors OR sector 命中 TECH_SECTORS 关键字
        is_main = bool(mainline_names) and any(m and (m in sector or sector in m) for m in mainline_names)
        if not is_main:
            is_main = any(t in sector for t in TECH_SECTORS)
        c["is_mainline"] = is_main
        try:
            tx = classify_sector_name(sector)
            c["taxonomy"] = {"l1": tx.get("l1", ""), "l2": tx.get("l2", "")}
        except Exception:
            c["taxonomy"] = {"l1": "", "l2": ""}
        c["market_cap_yi"] = round(c.get("market_cap", 0) / 1e8, 2)
        c["pe_ttm"] = 0  # 没拉的字段
        c["seat_aliases"] = []  # 不拉 lhb
        # R70 · 业绩同比 (跨日期共享, 加速回测)
        code = c.get("code", "")
        if code and code not in _FINANCE_CACHE:
            cached = _finance_redis_get(code)
            if cached:
                _FINANCE_CACHE[code] = cached
            else:
                try:
                    _FINANCE_CACHE[code] = fetch_finance_growth(code) or {}
                    _finance_redis_set(code, _FINANCE_CACHE[code])
                except Exception:
                    _FINANCE_CACHE[code] = {}
        fin = _FINANCE_CACHE.get(code, {}) or {}
        c["fin_latest_yoy"] = fin.get("latest_yoy")
        c["fin_turn_point"] = bool(fin.get("turn_point"))
        c["fin_yoy_trend"] = fin.get("yoy_trend", "FLAT")
    return pool


def _backtest_one_combo_one_day(combo_id: str, date_str: str, tail30_cache: dict | None = None) -> dict:
    """回测一天一个套餐。

    两种模式:
    1. **any_pass** (默认): 任一规则通过 → 视为命中, 算 T+N 胜率。这是真实"用了某条规则买入"的胜率。
    2. **all_pass** (严格 AND): 所有规则都通过才算命中。
    """
    combo = _yl.combo_by_id(combo_id)
    if not combo:
        return {"combo": combo_id, "date": date_str, "hits_any": 0, "hits_all": 0, "error": "no_combo"}
    try:
        pool = fetch_zt_pool(date_str) or []
    except Exception as e:
        return {"combo": combo_id, "date": date_str, "hits_any": 0, "hits_all": 0, "error": str(e)}
    if not pool:
        return {"combo": combo_id, "date": date_str, "hits_any": 0, "hits_all": 0, "error": "no_zt_pool"}
    pool = _enrich_zt_pool(pool, hot_sectors=[])

    def _one_stock(c):
        try:
            dims = _compute_kline_dims_for_date(c["code"], date_str)
            evals = [_rule_eval(rid, c, dims, None) for rid in combo["rules"]]
            any_pass = any(e["passed"] for e in evals)
            all_pass = all(e["passed"] for e in evals)
            entry_close = dims.get("close") or c.get("limit_price", 0) or 0
            if entry_close <= 0:
                return None
            all_kl = _fetch_full_kline(c["code"], date_str, "20991231")
            entry_idx = None
            for i, k in enumerate(all_kl):
                if k["date"].replace("-", "") == date_str:
                    entry_idx = i; break
            if entry_idx is None and all_kl:
                entry_idx = max([i for i, k in enumerate(all_kl) if k["date"].replace("-", "") <= date_str] or [len(all_kl)-1])
            entry_close_real = all_kl[entry_idx]["close"] if (entry_idx is not None and all_kl) else entry_close
            wr = _calc_winrate(all_kl, entry_idx, entry_close_real) if entry_idx is not None else {}
            return {
                "code": c["code"], "name": c.get("name", ""),
                "streak": c.get("streak", 0), "sector": c.get("sector", ""),
                "seal_ratio": c.get("seal_ratio_pct", 0),
                "close": entry_close_real, "wr": wr,
                "any_pass": any_pass, "all_pass": all_pass,
                "pass_n": sum(1 for e in evals if e["passed"]),
                "rule_n": len(evals),
                "evals": [{"rid": combo["rules"][i], "passed": evals[i]["passed"], "note": evals[i]["note"]}
                          for i in range(len(evals))],
            }
        except Exception:
            return None

    all_records = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for r in ex.map(_one_stock, pool):
            if r:
                all_records.append(r)

    # any_pass: 至少一条规则过 → 当日命中
    any_hits = [h for h in all_records if h.get("any_pass")]
    all_hits = [h for h in all_records if h.get("all_pass")]

    def _count(records, label, win_set=("WIN", "HOLD-WIN"), loss_set=("LOSS", "HOLD-LOSS")):
        win = sum(1 for h in records if h.get("wr", {}).get(label) in win_set)
        loss = sum(1 for h in records if h.get("wr", {}).get(label) in loss_set)
        n = len(records)
        return win, loss, n

    return {
        "combo": combo_id, "date": date_str,
        "hits_any": len(any_hits), "hits_all": len(all_hits),
        "samples_any": any_hits[:6],
        "samples_all": all_hits[:6],
        **{f"any_{k}": v for k, v in [
            ("t1w", _count(any_hits, "T+1")[0]), ("t1l", _count(any_hits, "T+1")[1]), ("t1n", _count(any_hits, "T+1")[2]),
            ("t3w", _count(any_hits, "T+3")[0]), ("t3l", _count(any_hits, "T+3")[1]), ("t3n", _count(any_hits, "T+3")[2]),
            ("t5w", _count(any_hits, "T+5")[0]), ("t5l", _count(any_hits, "T+5")[1]), ("t5n", _count(any_hits, "T+5")[2]),
        ]},
        "any_records": any_hits,  # 全部命中, 供汇总阶段聚合
        "all_records": all_hits,
    }


# ─── 多线程套件下载 (尾盘 5min) ────────────
def _prewarm_tail30(codes: list[str]) -> dict:
    """一次性拉今日 5min 尾盘, 缓存起来给所有日期复用 (假设最近 1 个交易日)。"""
    cache = {}
    def _do(code):
        try:
            r = fetch_tail_30min(code)
            return code, r
        except Exception:
            return code, None
    with ThreadPoolExecutor(max_workers=4) as ex:
        for code, r in ex.map(_do, codes):
            if r:
                cache[code] = r
    return cache


def main():
    days = int(os.environ.get("YEREN_BT_DAYS", "30"))
    import datetime as dt
    today = dt.datetime.now().strftime("%Y%m%d")
    # 取今天之前的最近 N 个交易日 (YYYY-MM-DD → YYYYMMDD)
    all_dates = sorted(fetch_trade_dates() or set())
    all_dates = [d for d in all_dates if d.replace("-", "") <= today]
    dates = all_dates[-days:]
    print(f"R67 · 回测 {days} 天 ({dates[0]} ~ {dates[-1]}), 5 个套餐", flush=True)

    # 先 warm 尾盘 5min (取最近的真实交易日池, 非未来)
    last_working_date = dates[-1].replace("-", "")
    today_pool = fetch_zt_pool(last_working_date) or []
    tail_cache = _prewarm_tail30([c["code"] for c in today_pool[:30]])
    print(f"尾盘 5min warmed ({last_working_date}): {len(tail_cache)} 只", flush=True)

    out = {}
    for cid in ("C1", "C2", "C3", "C4", "C5"):
        results = []
        for d in dates:
            d_ymd = d.replace("-", "")  # YYYY-MM-DD → YYYYMMDD
            r = _backtest_one_combo_one_day(cid, d_ymd, tail_cache)
            results.append(r)
            print(f"  {cid} {d_ymd} hits={r.get('hits', 0)} t1w={r.get('t1_win', 0)}/{r.get('hits', 0)}", flush=True)
        out[cid] = results

    # 汇总: any-pass 与 all-pass 各自的胜率 + 期望值 EV
    # EV = 胜率 × 平均盈利 - 亏率 × 平均亏损 (基于每只股的真实 T+N 收益)
    summary = {}
    for cid, rs in out.items():
        n_days = sum(1 for r in rs if not r.get("error", "").startswith("no"))

        def _ev_for_set(records: list[dict]) -> dict:
            """算一组记录的胜率/亏率/EV/期望最高/期望最低, 三个时间窗 T+1/T+3/T+5。

            2026-08-12: BUG 修复 — 原版用 `n = len(records)` 作分母,但 records 中可能
            含 wr 字段缺失的票(entry_idx 越界 / 无 K 线 / 当日停牌),导致 wr 偏低 6-10%。
            修复: wr / loss / wr_profit / avg_pct 都用 **有效样本数** n_valid
            (即 wr.{label}_pct is not None 的子集) 作分母。
            修复后 C1/C2 真实 WR ≈ 60-68%, 跟 avg_pct=+4.26% 自洽。
            """
            n = len(records)
            if n == 0:
                return {"n": 0, "wr": None, "loss": None, "ev_pct": None,
                        "max_pct": None, "min_pct": None, "wr_profit": None, "avg_pct": None}
            out = {"n": n}
            for label in ("T+1", "T+3", "T+5"):
                # 有效样本: wr.{label} 状态可判 + pct 字段存在
                valid = [h for h in records
                         if h.get("wr", {}).get(label) in ("WIN", "HOLD-WIN", "LOSS", "HOLD-LOSS", "FLAT")
                         and h.get("wr", {}).get(f"{label}_pct") is not None]
                nv = len(valid)
                if nv == 0:
                    out[f"{label.replace('+', '_').lower()}_wr"] = None
                    out[f"{label.replace('+', '_').lower()}_loss"] = None
                    out[f"{label.replace('+', '_').lower()}_wr_profit"] = None
                    out[f"{label.replace('+', '_').lower()}_avg_pct"] = None
                    out[f"{label.replace('+', '_').lower()}_ev_pct"] = None
                    out[f"{label.replace('+', '_').lower()}_max_pct"] = None
                    out[f"{label.replace('+', '_').lower()}_min_pct"] = None
                    continue
                # R71 严格五态口径
                wins = [h for h in valid if h["wr"][label] in ("WIN", "HOLD-WIN")]
                losses = [h for h in valid if h["wr"][label] in ("LOSS", "HOLD-LOSS")]
                nw = len(wins); nl = len(losses)
                wr = nw / nv
                lr = nl / nv
                def _g(d, k, default=0.0):
                    v = d.get(k); return v if v is not None else default
                avg_win = (sum(_g(h["wr"], f"{label}_max") for h in wins) / nw) if nw else 0.0
                avg_loss = (sum(_g(h["wr"], f"{label}_min") for h in losses) / nl) if nl else 0.0
                ev = wr * avg_win + lr * avg_loss
                # 用户口径"赚钱就是盈利" — 期末 last_pct > 0
                profit_n = sum(1 for h in valid if h["wr"][f"{label}_pct"] > 0)
                wr_profit = profit_n / nv
                avg_close = sum(h["wr"][f"{label}_pct"] for h in valid) / nv
                avg_max = sum(_g(h["wr"], f"{label}_max") for h in valid) / nv
                avg_min = sum(_g(h["wr"], f"{label}_min") for h in valid) / nv
                suf = label.replace("+", "_").lower()
                out[f"{suf}_wr"] = round(wr, 3)
                out[f"{suf}_loss"] = round(lr, 3)
                out[f"{suf}_wr_profit"] = round(wr_profit, 3)
                out[f"{suf}_ev_pct"] = round(ev * 100, 2)
                out[f"{suf}_avg_pct"] = round(avg_close * 100, 2)
                out[f"{suf}_max_pct"] = round(avg_max * 100, 2)
                out[f"{suf}_min_pct"] = round(avg_min * 100, 2)
            return out

        # any_pass 聚合
        all_any = [h for r in rs for h in (r.get("any_records") or [])]
        any_ev = _ev_for_set(all_any)

        # all_pass 聚合
        all_all = [h for r in rs for h in (r.get("all_records") or [])]
        all_ev = _ev_for_set(all_all)

        # per-rule 命中+WR+EV
        rule_stats = {}
        for rid in _yl.combo_by_id(cid)["rules"]:
            hits = [h for h in all_any if any(ev["rid"] == rid and ev["passed"] for ev in h["evals"])]
            rid_ev = _ev_for_set(hits)
            rule_stats[rid] = {
                "n_hits": len(hits),
                "win_rate_t1": rid_ev.get("t_1_wr"),
                "loss_rate_t1": rid_ev.get("t_1_loss"),
                "ev_pct_t1": rid_ev.get("t_1_ev_pct"),
                "avg_pct_t1": rid_ev.get("t_1_avg_pct"),
                "max_pct_t1": rid_ev.get("t_1_max_pct"),
                "min_pct_t1": rid_ev.get("t_1_min_pct"),
            }

        summary[cid] = {
            "n_days_ok": n_days,
            "any_pass": any_ev,
            "all_pass": all_ev,
            "per_rule": rule_stats,
        }

    Path("/tmp/yeren_bt.json").write_text(json.dumps({"raw": out, "summary": summary},
                                                      ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    print("\n=== 汇总 (期望值视角) ===")
    best = []
    for cid, s in summary.items():
        any_p = s["any_pass"]
        ev1 = any_p.get("t_1_ev_pct")
        ev3 = any_p.get("t_3_ev_pct")
        ev5 = any_p.get("t_5_ev_pct")
        if ev1 is not None:
            best.append((cid, ev1, ev3, ev5))
        print(f"  {cid}: any n={any_p.get('n', 0)} "
              f"T+1WR={any_p.get('t_1_wr', 0):.0%} EV={ev1:+.2f}% "
              f"T+3EV={ev3:+.2f}% T+5EV={ev5:+.2f}%")
    if best:
        best.sort(key=lambda x: x[1] or -999, reverse=True)
        print(f"\n★ 期望值最高套餐: {best[0][0]} (T+1 EV = {best[0][1]:+.2f}%)")
    Path("/tmp/yeren_bt_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写 /tmp/yeren_bt.json 和 /tmp/yeren_bt_summary.json")


if __name__ == "__main__":
    main()
