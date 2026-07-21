"""
周线擒牛 · 5 大信号检测
─────────────────────
1. 三星探底     — 长期下跌后低位横盘,周线 3 根小十字星 + 站稳 5 周均线
2. 站稳5周线    — 不再创新低 + 放量阳线站稳 5 周均线
3. 突破震荡平台  — 周收盘突破前 4-5 周高点
4. 均线方向     — 5周 + 20周均线均向上拐头,楼梯排列
5. 周线堆量     — 连续 3 周以上成交量温和放大

数据源:日线 K 线聚合 (5 交易日 ≈ 1 周),复用 stock_kline_loader,无外部 API 依赖。
"""
from __future__ import annotations

import datetime as _dt
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple

log = logging.getLogger(__name__)


# ─── 周线聚合 ───────────────────────────────────────────────────────────────

def _to_weekly(daily: List[dict]) -> List[dict]:
    """日线 dicts → 周线 dicts (按 ISO 年+周数分组)

    每周: open=首.open, close=末.close, high=max, low=min, volume=sum, amount=sum
    过滤周内交易日 < 3 的 (节假日周)
    返回倒序 (新→旧),最后一周是当前周
    """
    if not daily:
        return []

    buckets: Dict[Tuple[int, int], List[dict]] = {}
    for r in daily:
        d = str(r.get("date") or "")[:10]
        if len(d) != 10:
            continue
        try:
            y, m, dd = int(d[:4]), int(d[5:7]), int(d[8:10])
            iso_year, iso_week, _ = _dt.date(y, m, dd).isocalendar()
        except Exception:
            continue
        buckets.setdefault((iso_year, iso_week), []).append(r)

    weeks = []
    for key in sorted(buckets.keys()):
        bars = sorted(buckets[key], key=lambda x: x.get("date") or "")
        if len(bars) < 3:
            continue  # 节假日周跳过
        op = bars[0].get("open") or 0
        cl = bars[-1].get("close") or 0
        hi = max((b.get("high") or 0) for b in bars)
        lo = min((b.get("low") or 0) for b in bars)
        vol = sum((b.get("volume") or 0) for b in bars)
        amt = sum((b.get("amount") or 0) for b in bars)
        weeks.append({
            "iso_year": key[0],
            "iso_week": key[1],
            "date_start": bars[0]["date"],
            "date_end": bars[-1]["date"],
            "open": op,
            "close": cl,
            "high": hi,
            "low": lo,
            "volume": vol,
            "amount": amt,
            "change_pct": round((cl / op - 1) * 100, 2) if op else 0,
            "bars_in_week": len(bars),
        })

    # 计算 wma5 / wma20 (rolling on weekly close)
    closes = [w["close"] for w in weeks]
    for i, w in enumerate(weeks):
        w["wma5"] = round(sum(closes[max(0, i - 4):i + 1]) / min(i + 1, 5), 3) if i + 1 else 0
        w["wma20"] = round(sum(closes[max(0, i - 19):i + 1]) / min(i + 1, 20), 3) if i + 1 else 0
    return weeks


# ─── 5 个 pattern 检测器 (输入周线数组, 输出 (bool, reason:str)) ───────────

def p_sanxing_taodi(wk: List[dict]) -> Tuple[bool, str]:
    """三星探底: 长期下跌后, 最近 3 周小十字星 + 站稳 5 周均线"""
    if len(wk) < 6:
        return False, "周线不足 6 周"
    last3 = wk[-3:]
    # 三星: 实体 / 收盘 都 < 3%
    small_body = all(
        abs(w["close"] - w["open"]) / max(w["close"], 0.01) < 0.03
        for w in last3
    )
    # 之前跌过 (3 周前 close 比 1 周前高)
    declined_before = wk[-6]["close"] > wk[-3]["close"] * 1.02
    # 当前站上 5 周均线
    above_wma5 = wk[-1]["close"] > wk[-1].get("wma5", 0) > 0
    if small_body and declined_before and above_wma5:
        return True, f"三周小十字星 + 收盘 {wk[-1]['close']:.2f} 站稳 5W 均线 {wk[-1]['wma5']:.2f}"
    return False, ""


def p_zhanwen_5w(wk: List[dict]) -> Tuple[bool, str]:
    """站稳5周线: 不再创新低 + 放量阳线站稳 5 周均线"""
    if len(wk) < 4:
        return False, "周线不足 4 周"
    last = wk[-1]
    # 阳线
    is_bullish = last["close"] > last["open"] * 1.005
    # 放量 (最近 1 周 volume > 前 3 周均量 1.3x)
    avg_vol_prev3 = sum(w["volume"] for w in wk[-4:-1]) / 3
    vol_up = last["volume"] > avg_vol_prev3 * 1.3
    # 站上 5 周均线
    above_wma5 = last["close"] > last.get("wma5", 0) > 0
    # 不再创新低 (本周 low >= 前 3 周 low 最小值)
    no_new_low = last["low"] >= min(w["low"] for w in wk[-4:-1])
    if is_bullish and vol_up and above_wma5 and no_new_low:
        return True, f"放量阳线收 {last['close']:.2f}, 站上 5W 均线 {last['wma5']:.2f}, 不再创新低"
    return False, ""


def p_tupo_pingtai(wk: List[dict], aggressive: bool = False) -> Tuple[bool, str]:
    """突破震荡平台: 周收盘突破前 4-5 周高点

    aggressive=True: 激进版, 只要本周收盘 > 前三周最高收盘价就触发
                      (行情好时更早上车)
    """
    if len(wk) < 6:
        return False, "周线不足 6 周"
    last = wk[-1]
    if aggressive:
        prev_close = max(w["close"] for w in wk[-4:-1])  # 前三周最高收盘价
        if last["close"] > prev_close:
            return True, f"周收 {last['close']:.2f} 突破前 3 周收盘 {prev_close:.2f} (激进)"
        return False, ""
    prev_high = max(w["high"] for w in wk[-6:-1])
    if last["close"] > prev_high:
        return True, f"周收 {last['close']:.2f} 突破前 5 周高点 {prev_high:.2f}"
    return False, ""


def p_junxian_fangxiang(wk: List[dict]) -> Tuple[bool, str]:
    """均线方向: 5周 + 20周均线均向上拐头, 楼梯排列, 量能温和放大"""
    if len(wk) < 22:
        return False, "周线不足 22 周 (需 20W MA)"
    last = wk[-1]
    # 5W MA 连续 3 周上升
    wma5_rising = all(
        wk[-1 - i]["wma5"] > wk[-2 - i]["wma5"] for i in range(3)
        if wk[-2 - i]["wma5"] > 0
    )
    # 20W MA 连续 2 周上升
    wma20_rising = all(
        wk[-1 - i]["wma20"] > wk[-2 - i]["wma20"] for i in range(2)
        if wk[-2 - i]["wma20"] > 0
    )
    # 楼梯排列 5W > 20W
    staircase = last["wma5"] > last["wma20"] > 0
    # 量能温和放大 (近 3 周递增)
    vol_rising = wk[-1]["volume"] > wk[-2]["volume"] > wk[-3]["volume"]
    if wma5_rising and wma20_rising and staircase and vol_rising:
        return True, f"5W {last['wma5']:.2f} > 20W {last['wma20']:.2f}, 双线向上拐头, 量能递增"
    return False, ""


def p_zhouxian_duiliang(wk: List[dict]) -> Tuple[bool, str]:
    """周线堆量: 连续 3 周以上成交量温和放大"""
    if len(wk) < 4:
        return False, "周线不足 4 周"
    # 连续 3 周递增
    rising3 = wk[-1]["volume"] > wk[-2]["volume"] > wk[-3]["volume"]
    # 本周量 > 1 周前 1.5 倍
    pile_up = wk[-1]["volume"] > wk[-4]["volume"] * 1.5
    if rising3 and pile_up:
        return True, f"3 周量能递增, 本周 {wk[-1]['volume']:.0f} 是 4 周前 {wk[-4]['volume']:.0f} 的 {wk[-1]['volume'] / max(wk[-4]['volume'], 1):.1f}x"
    return False, ""


# ─── 公共 API ──────────────────────────────────────────────────────────────

PATTERNS: Dict[str, Tuple[str, callable]] = {
    "sanxing_taodi":              ("三星探底",     p_sanxing_taodi),
    "zhanwen_5w":                 ("站稳5周线",    p_zhanwen_5w),
    "tupo_pingtai":               ("突破震荡平台",  lambda wk: p_tupo_pingtai(wk, aggressive=False)),
    "tupo_pingtai_aggressive":    ("突破3周收盘(激进)", lambda wk: p_tupo_pingtai(wk, aggressive=True)),
    "junxian_fangxiang":          ("均线方向",     p_junxian_fangxiang),
    "zhouxian_duiliang":          ("周线堆量",     p_zhouxian_duiliang),
}


def analyze_one(code: str, kline_loader=None) -> dict:
    """单股周线分析。

    返回 {code, matched:[key], reasons:{key:str}, count, weekly_last:{...}}
    kline_loader: 可选外部注入, 默认从 .server 懒加载
    """
    if kline_loader is None:
        try:
            from .server import stock_kline_loader
        except Exception:
            import tuixue_v3.web.server as _srv
            kline_loader = _srv.stock_kline_loader
    out = {"code": code, "matched": [], "reasons": {}, "count": 0, "weekly_last": None, "_skip": False}
    try:
        daily = kline_loader(code, 250)
    except Exception as e:
        out["_skip"] = True
        out["_err"] = str(e)[:80]
        return out
    if not daily or len(daily) < 30:
        out["_skip"] = True
        out["_err"] = "K线 < 30 天"
        return out
    wk = _to_weekly(daily)
    if len(wk) < 6:
        out["_skip"] = True
        out["_err"] = "周线 < 6 周"
        return out
    out["weekly_last"] = {
        "date_start": wk[-1]["date_start"],
        "date_end": wk[-1]["date_end"],
        "close": wk[-1]["close"],
        "open": wk[-1]["open"],
        "high": wk[-1]["high"],
        "low": wk[-1]["low"],
        "volume": wk[-1]["volume"],
        "change_pct": wk[-1]["change_pct"],
        "wma5": wk[-1]["wma5"],
        "wma20": wk[-1]["wma20"],
    }
    out["weekly_n"] = len(wk)
    for key, (label, fn) in PATTERNS.items():
        try:
            ok, reason = fn(wk)
        except Exception:
            ok, reason = False, ""
        if ok:
            out["matched"].append(key)
            out["reasons"][key] = reason
    out["count"] = len(out["matched"])
    return out


def scan_universe(codes: List[str] = None, max_workers: int = 8) -> dict:
    """全市场扫描。

    codes: 股票代码列表 (None = 默认从 all_stocks._build_universe() 取, cap 800)
    返回 {signals:[...], by_pattern:{key:[codes]}, total_scanned, ts, took_ms}
    """
    import time
    if codes is None:
        try:
            from .all_stocks import _build_universe
            u = _build_universe()
            _name_map = {}
            if isinstance(u, tuple):
                _name_map = u[1] if len(u) > 1 else {}
                u = u[0] if u else {}
            codes = list(u.keys())[:200]
        except Exception as e:
            log.warning("[weekly-bull] _build_universe failed: %s", e)
            codes = []
            _name_map = {}
    else:
        _name_map = {}

    if not codes:
        codes = ["000001", "002747", "300750", "600519", "601318", "000333", "600036", "601012"]

    t0 = time.time()
    signals: List[dict] = []
    by_pattern: Dict[str, List[str]] = {k: [] for k in PATTERNS}

    # 取 stock_kline_loader 传给子线程 (避免 executor 内 .server import 问题)
    try:
        from .server import stock_kline_loader as _loader
    except Exception:
        try:
            import tuixue_v3.web.server as _srv
            _loader = _srv.stock_kline_loader
        except Exception:
            _loader = None

    def _analyze(code):
        return analyze_one(code, _loader)

    # 4 workers: 避免 macOS 端口耗尽 (8 workers × 11 数据源 × retry = 88 并发连接)
    max_w = min(max_workers or 8, 4)
    ex = ThreadPoolExecutor(max_workers=max_w)
    try:
        futs = {ex.submit(_analyze, code): code for code in codes}
        try:
            for fut in as_completed(futs, timeout=25):
                try:
                    r = fut.result(timeout=4)
                except Exception:
                    continue
                if not r or r.get("_skip"):
                    continue
                if r.get("matched"):
                    signals.append({
                        "code": r["code"],
                        "name": _name_map.get(r["code"]) or _name_map.get(r["code"][:6], ""),
                        "matched": r["matched"],
                        "reasons": r["reasons"],
                        "count": r["count"],
                        "weekly_last": r["weekly_last"],
                    })
                    for k in r["matched"]:
                        if k in by_pattern:
                            by_pattern[k].append(r["code"])
        except TimeoutError:
            log.warning(f"[weekly-bull] scan timeout (20s), got {len(signals)} signals so far")
    finally:
        ex.shutdown(wait=False)  # 不阻塞: 慢 worker 丢弃

    # 质量评分 (0-100)
    _WB_SCORE_WEIGHTS = {
        "sanxing_taodi": 30, "tupo_pingtai": 25, "tupo_pingtai_aggressive": 15,
        "zhanwen_5w": 20, "zhouxian_duiliang": 15, "junxian_fangxiang": 10,
    }
    for s in signals:
        score = sum(_WB_SCORE_WEIGHTS.get(p, 0) for p in (s.get("matched") or []))
        s["score"] = min(score, 100)

    # 按 score desc + count desc + code asc 排
    signals.sort(key=lambda x: (-(x.get("score") or 0), -x["count"], x["code"]))

    return {
        "signals": signals,
        "by_pattern": by_pattern,
        "total_scanned": len(codes),
        "matched_count": len(signals),
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "took_ms": int((time.time() - t0) * 1000),
    }
