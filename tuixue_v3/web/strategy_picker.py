"""
策略选股器 · 3 大策略全市场扫描
─────────────────────────────────
1. 周线擒牛     — 复用 web.weekly_bull.analyze_one
2. 1/3 回升位   — 复用 web.recovery_level.analyze_recovery
3. 5日线放量    — 日线: 最近 5 日放量 + 收盘站上 5 日均线

数据源: 复用 stock_kline_loader,无外部 API 依赖。

组合模式:
  - AND (推荐): 3 策略全满足
  - OR:         任一策略命中

返回:
  {
    signals: [...],
    by_strategy: {wb: [...], rl: [...], ma5: [...]},
    summary: {wb_min, rl_near, ma5_breakout, mode, ...},
    total_scanned, matched_count, took_ms, ts,
  }
"""
from __future__ import annotations

import datetime as _dt
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


# ─── 5日线5原则 #3-#5 状态计算 ──────────────────────────────────────────────

def compute_ma5_principles(daily: List[dict]) -> dict:
    """计算 5日线5原则 #3-#5 状态。

    返回:
    {
      has_kline: bool,
      deviation_pct: float,   # 偏离 5 日线百分比 (正=在上方)
      below_ma5_days: int,    # 连续收盘低于 5 日线的天数
      status: str,            # normal / deviated_up / deviated_dn / below_ma5
      principle_3_active: bool,  # 偏离 > 8%
      principle_4_active: bool,  # 收盘跌破 5 日线
      principle_5_active: bool,  # 连续 3 日站不回
    }
    """
    out = {
        "has_kline": False,
        "deviation_pct": 0,
        "below_ma5_days": 0,
        "status": "unknown",
        "principle_3_active": False,
        "principle_4_active": False,
        "principle_5_active": False,
    }
    if not daily or len(daily) < 6:
        return out

    last = daily[-1]
    close = last.get("close") or 0
    ma5 = last.get("ma5") or 0
    if close <= 0 or ma5 <= 0:
        return out

    out["has_kline"] = True
    ma5 = last["ma5"]
    deviation = round((close - ma5) / ma5 * 100, 2) if ma5 else 0
    out["deviation_pct"] = deviation

    # 连续低于 MA5 的天数
    below_count = 0
    for bar in reversed(daily):
        c = bar.get("close") or 0
        m5 = bar.get("ma5") or 0
        if c > 0 and m5 > 0 and c < m5:
            below_count += 1
        else:
            break
    out["below_ma5_days"] = below_count

    # #3: 明显偏离 (> 8%)
    out["principle_3_active"] = abs(deviation) > 8

    # #4: 收盘跌破 5 日线
    out["principle_4_active"] = close < ma5

    # #5: 连续 3 日站不回
    out["principle_5_active"] = below_count >= 3

    # 综合状态
    if below_count >= 3:
        out["status"] = "below_3d"
    elif close < ma5:
        out["status"] = "below_ma5"
    elif deviation > 8:
        out["status"] = "deviated_up"
    elif deviation < -8:
        out["status"] = "deviated_dn"
    else:
        out["status"] = "normal"
    return out


# ─── 5日线放量 + 站上 5 日线 检测器 ────────────────────────────────────────

def p_ma5_breakout(daily: List[dict]) -> Tuple[bool, str]:
    """5日线 5 原则 #1+#2: 最近 1 周放量 + 收盘站上 5 日均线。

    条件:
      1. 最近 1 根 (今日) 是放量阳线: close > open * 1.005, volume > 5日均量 * 1.3
      2. close > ma5 (5 日均线)
      3. ma5 本身向上拐头 (今日 ma5 > 昨日 ma5)
    """
    if not daily or len(daily) < 6:
        return False, "日线不足 6 天"
    last = daily[-1]
    prev5 = daily[-6:-1]
    close = last.get("close") or 0
    op = last.get("open") or 0
    vol = last.get("volume") or 0
    ma5 = last.get("ma5") or 0
    if close <= 0 or ma5 <= 0:
        return False, "数据不全"

    # 1) 放量阳线
    is_bullish = close > op * 1.005
    avg_vol_prev5 = sum(d.get("volume") or 0 for d in prev5) / 5 if prev5 else 0
    vol_up = vol > avg_vol_prev5 * 1.3 if avg_vol_prev5 > 0 else False
    if not (is_bullish and vol_up):
        return False, ""

    # 2) 站上 5 日线
    above_ma5 = close > ma5
    if not above_ma5:
        return False, ""

    # 3) ma5 向上拐头
    ma5_yest = daily[-2].get("ma5") or 0
    rising = ma5 > ma5_yest > 0
    if not rising:
        return False, ""

    return True, f"放量阳收 {close:.2f} (量 {vol/max(avg_vol_prev5,1):.1f}x), 站上 5 日线 {ma5:.2f}, MA5 拐头向上"


# ─── 单股综合判定 ───────────────────────────────────────────────────────────

def _score_signal(r: dict, wb_min: int = 1) -> dict:
    """给单股的全部信号打分 0-100, 返回 {total, wb, rl, ma5, breakdown}."""
    score: dict = {"total": 0, "wb": 0, "rl": 0, "ma5": 0, "max": 100, "breakdown": []}

    # 周线擒牛 (0-40)
    wb_score = 0
    if r.get("wb") and r["wb"].get("count", 0) > 0:
        cnt = r["wb"]["count"]
        # 基础分: 每个信号 10 分, cap 40
        wb_score = min(cnt * 10, 40)
        # 特定 pattern 加分
        pats = set(r["wb"].get("matched", []))
        if "sanxing_taodi" in pats:
            wb_score += 8  # 三星探底是最强反转信号
        if "tupo_pingtai" in pats:
            wb_score += 6  # 突破平台确认趋势
        if "zhanwen_5w" in pats:
            wb_score += 4  # 站稳 5 周线确认支撑
        if "zhouxian_duiliang" in pats:
            wb_score += 4  # 堆量确认主力介入
        if "junxian_fangxiang" in pats:
            wb_score += 2  # 均线方向辅助
        wb_score = min(wb_score, 40)
        score["breakdown"].append(f"周线 {r['wb']['count']}/5 + 形态 → {wb_score}分")
    score["wb"] = wb_score

    # 1/3 回升位 (0-30)
    rl_score = 0
    if r.get("rl") and r["rl"].get("level_1_3"):
        dist = abs(r["rl"].get("distance_to_level_1_3_pct") or 99)
        near = r["rl"].get("near_support", False)
        # 越靠近 1/3 位分越高
        if near:
            rl_score = 25 + (5 if dist < 1 else 0)  # <1% → 30, <3% → 25
        elif dist < 5:
            rl_score = 18
        elif dist < 10:
            rl_score = 10
        else:
            rl_score = 5
        rl_score = min(rl_score, 30)
        score["breakdown"].append(f"1/3位 距{dist:.1f}%{' [强支撑]' if near else ''} → {rl_score}分")
    score["rl"] = rl_score

    # 5日线放量 (0-30)
    ma5_score = 0
    if r.get("ma5") and r["ma5"].get("ok"):
        reason = r["ma5"].get("reason", "")
        # 放量倍数越高分越高
        import re as _re
        m = _re.search(r"量\s*([\d.]+)x", reason)
        vol_ratio = float(m.group(1)) if m else 1.5
        ma5_score = min(int(vol_ratio * 10) + 5, 30)
        score["breakdown"].append(f"MA5 放量 {vol_ratio:.1f}x → {ma5_score}分")
    score["ma5"] = ma5_score

    score["total"] = wb_score + rl_score + ma5_score
    return score


def analyze_one(code: str, kline_loader=None) -> dict:
    """单股 3 策略综合分析。

    返回:
      {
        code,
        wb: {matched, count} | None,
        rl: {has_signal, level_1_3, current_close, near_support} | None,
        ma5: {ok, reason} | {ok: False},
        matched_count: int (满足的策略数, max 3),
        matched_keys: [str]  (满足的策略 keys),
      }
    """
    from . import weekly_bull as _wb
    from . import recovery_level as _rl

    out: dict = {
        "code": code,
        "wb": None,
        "rl": None,
        "ma5": None,
        "matched_count": 0,
        "matched_keys": [],
        "_skip": False,
    }

    # 1) 周线擒牛
    try:
        wb_r = _wb.analyze_one(code, kline_loader)
        if wb_r and not wb_r.get("_skip"):
            out["wb"] = {
                "matched": wb_r.get("matched", []),
                "count": wb_r.get("count", 0),
                "reasons": wb_r.get("reasons", {}),
                "weekly_last": wb_r.get("weekly_last"),
            }
    except Exception as e:
        log.debug(f"[strategy-picker] {code} wb err: {e}")

    # 2) 1/3 回升位
    try:
        rl_r = _rl.analyze_recovery(code, kline_loader)
        if rl_r and not rl_r.get("_skip"):
            out["rl"] = {
                "has_signal": rl_r.get("has_signal", False),
                "level_1_3": rl_r.get("level_1_3"),
                "level_1_2": rl_r.get("level_1_2"),
                "level_2_3": rl_r.get("level_2_3"),
                "current_close": rl_r.get("current_close"),
                "near_support": rl_r.get("near_support", False),
                "distance_to_level_1_3_pct": rl_r.get("distance_to_level_1_3_pct"),
                "A": rl_r.get("A"),
                "B": rl_r.get("B"),
                "A_date": rl_r.get("A_date"),
                "B_date": rl_r.get("B_date"),
                "change_pct": rl_r.get("change_pct"),
                "explanation": rl_r.get("explanation"),
            }
    except Exception as e:
        log.debug(f"[strategy-picker] {code} rl err: {e}")

    # 3) 5日线放量 + 5日线5原则 #3-#5 状态
    try:
        if kline_loader is None:
            from .server import stock_kline_loader
            kline_loader = stock_kline_loader
        daily = kline_loader(code, 10) or []
        if daily and len(daily) >= 6:
            ok, reason = p_ma5_breakout(daily)
            out["ma5"] = {"ok": ok, "reason": reason}
            # 5日线5原则 #3-#5 状态
            out["ma5_principles"] = compute_ma5_principles(daily)
    except Exception as e:
        log.debug(f"[strategy-picker] {code} ma5 err: {e}")

    # matched_count
    keys = []
    if out["wb"] and out["wb"]["count"] >= 1:
        keys.append("wb")
    if out["rl"] and out["rl"].get("near_support"):
        keys.append("rl")
    if out["ma5"] and out["ma5"]["ok"]:
        keys.append("ma5")
    out["matched_keys"] = keys
    out["matched_count"] = len(keys)
    return out


# ─── 全市场扫描 ─────────────────────────────────────────────────────────────

def _make_cached_loader():
    """返回一个 per-call LRU 缓存的 kline_loader, 确保同一扫描内每只股票只加载一次 K 线."""
    _cache: dict = {}

    def _load(code, ndays=250):
        if code not in _cache:
            try:
                from .server import stock_kline_loader
                _cache[code] = stock_kline_loader(code, ndays) or []
            except Exception:
                _cache[code] = []
        return _cache[code]

    return _load


def scan_strategies(
    codes: Optional[List[str]] = None,
    wb_min: int = 1,
    rl_near: bool = True,
    ma5_breakout: bool = True,
    mode: str = "and",
    min_matched: int = 1,
    max_workers: int = 8,
) -> dict:
    """全市场策略扫描。

    Args:
        codes: 股票代码列表 (None = 默认全市场 cap 500)
        wb_min: 周线擒牛至少命中 N 个 (0-5)
        rl_near: 是否要求接近 1/3 回升位
        ma5_breakout: 是否要求 5日线放量站上
        mode: "and" (全满足) / "or" (任一)
        min_matched: 最少满足的策略数 (1-3)
    """
    if codes is None:
        try:
            from .all_stocks import _build_universe
            u = _build_universe()
            if isinstance(u, tuple):
                u = u[0] if u else {}
            codes = list((u or {}).keys())[:150]  # cap 150 避免 cold scan 超时
        except Exception as e:
            log.warning(f"[strategy-picker] _build_universe failed: {e}")
            codes = []

    if not codes:
        codes = ["000001", "002747", "300750", "600519", "601318"]

    # 取 stock name map (code → name)
    try:
        from .all_stocks import _build_universe
        _u, _nm = _build_universe()
        _name_map: dict = _nm or {}
    except Exception:
        _name_map = {}

    # 每只股票只加载一次 K 线, 3 策略复用
    _loader = _make_cached_loader()

    t0 = time.time()
    signals: List[dict] = []
    by_strategy: Dict[str, List[str]] = {"wb": [], "rl": [], "ma5": []}
    skipped = 0
    failed = 0

    def _analyze(code):
        return analyze_one(code, _loader)

    ex = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futs = {ex.submit(_analyze, code): code for code in codes}
        try:
            for fut in as_completed(futs, timeout=20):
                try:
                    r = fut.result(timeout=3)
                except Exception:
                    failed += 1
                    continue
                if not r or r.get("_skip"):
                    skipped += 1
                    continue

                # 判定是否满足条件
                matched = []
                if r.get("wb") and r["wb"].get("count", 0) >= wb_min:
                    matched.append("wb")
                if r.get("rl") and rl_near and r["rl"].get("near_support"):
                    matched.append("rl")
                if r.get("ma5") and ma5_breakout and r["ma5"].get("ok"):
                    matched.append("ma5")

                if len(matched) < min_matched:
                    continue
                if mode == "and" and len(matched) < 3:
                    continue
                if mode == "or" and len(matched) < 1:
                    continue

                sig = {
                    "code": r["code"],
                    "name": _name_map.get(r["code"]) or _name_map.get(r["code"][:6], ""),
                    "matched_keys": matched,
                    "matched_count": len(matched),
                    "wb": r.get("wb"),
                    "rl": r.get("rl"),
                    "ma5": r.get("ma5"),
                }
                sig["score"] = _score_signal(sig, wb_min)
                signals.append(sig)
                for k in matched:
                    by_strategy[k].append(r["code"])
        except TimeoutError:
            log.warning(f"[strategy-picker] scan timeout (20s), got {len(signals)} so far")
    finally:
        ex.shutdown(wait=False)  # 不阻塞: 慢 worker 丢弃

    # 按总分 desc + matched_count desc + code asc
    signals.sort(key=lambda x: (-(x.get("score") or {}).get("total", 0), -x["matched_count"], x["code"]))

    return {
        "signals": signals,
        "by_strategy": by_strategy,
        "summary": {
            "wb_min": wb_min,
            "rl_near": rl_near,
            "ma5_breakout": ma5_breakout,
            "mode": mode,
            "min_matched": min_matched,
        },
        "total_scanned": len(codes),
        "skipped": skipped,
        "failed": failed,
        "matched_count": len(signals),
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "took_ms": int((time.time() - t0) * 1000),
    }
