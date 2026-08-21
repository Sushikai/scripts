"""web/intraday_5d_helpers.py — R136 方向C: stock_intraday_5d 内 _load 拆 4 子函数

模块抽出原因:
  原 stock_intraday_5d._load 内嵌 ~180 行,包含 4 个清晰子任务
  (load_daily_5d / load_today_ticks / aggregate_5d_summary / stage_cache_write)。
  端点本身只负责 cache + envelope + 超时兜底, 业务逻辑塞在 endpoint 内不易单测。

设计:
  4 个 helper 都是纯函数, 依赖通过参数注入 (caller 提供 cache_key, code, today_str)。
  避免 web.intraday_5d_helpers ↔ web.server 循环 import。

用法 (server.py):
  from .intraday_5d_helpers import (
      load_daily_5d, aggregate_5d_summary, load_today_ticks, stage_cache_write,
  )

  # 在 _load() 内:
  daily_5d_rows = load_daily_5d(code, recent5, repo_root)
  aggregate_5d_summary(out, daily_5d_rows, seal_by_date)
  load_today_ticks(code, today_str, out)
  stage_cache_write(cache_key_today, cache_key_hist, out, fresh)
"""
from __future__ import annotations
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .server import _safe_float  # R2000.17: 补拉日线时 robust 转 float

_log = logging.getLogger(__name__)


def _fetch_prev_close_safe(code: str) -> Optional[float]:
    """R2000.42 (2026-08-17): 取个股昨收 — 用于 daily_5d 今日行 change_pct 兜底.
    历史日 daily_dict 若为脏数据 (cache 未刷新, 全 0), 今日行计算 chg 时 fallback 用.
    """
    try:
        from .. import lib_common as _lc
        q = _lc.fetch_realtime(code)
        if q:
            for k in ("昨收", "prev_close"):
                v = q.get(k)
                if v is not None and float(v) > 0:
                    return float(v)
    except Exception as e:
        _log.debug(f"_fetch_prev_close_safe {code} 失败: {e}")
    return None


def load_daily_5d(code: str, recent5: List[str], repo_root: Path) -> Tuple[Dict[str, Dict], Dict[str, Dict]]:
    """
    加载 5 日日线 (本地 cache) + 5 日涨停池 (并行 fetch) → 返回 (daily_dict, seal_by_date)。

    daily_dict: {date_str → row_dict}   (含 开/高/低/收/量/额)
    seal_by_date: {date_str → {was_limit_up, streak, sealed_amount, total_amount,
                                seal_ratio_pct, first_seal_time, burst_count, sector}}
    """
    from .. import multi_source_fetchers as msf

    cache_dir = repo_root / "cache"
    daily_dict: Dict[str, Dict] = {}
    for sub in (f"daily_{code}_130.json", f"daily_{code}_400.json"):
        cp = cache_dir / sub
        if cp.exists():
            try:
                with cp.open() as f:
                    rows = json.load(f)
                daily_dict = {r["日期"]: r for r in rows if "日期" in r}
                break
            except Exception as e:
                _log.warning(f"读 daily cache 失败: {e}")

    # R2000.17: cache 命中但缺 recent5 任意一日 (cache 130 天尾可能是 1-2 月前)
    # → 走 stock_kline_loader 优先 recent5 列表,再用上游结果补 daily_dict
    missing = [d for d in recent5 if d not in daily_dict]
    if missing:
        try:
            from .. import lib_common as _lc
            needed = max(len(recent5) + 20, 30)
            df = _lc.fetch_daily(code, days=needed)
            if df is not None and not df.empty:
                for _, r in df.iterrows():
                    d_s = str(r.get("日期", ""))[:10]
                    if d_s and d_s not in daily_dict:
                        daily_dict[d_s] = {
                            "日期": d_s,
                            "开盘":   _safe_float(r.get("开盘")),
                            "收盘":   _safe_float(r.get("收盘")),
                            "最高":   _safe_float(r.get("最高")),
                            "最低":   _safe_float(r.get("最低")),
                            "成交量": _safe_float(r.get("成交量")),
                            "成交额": _safe_float(r.get("成交额")),
                            "涨跌幅": _safe_float(r.get("涨跌幅")),
                        }
        except Exception as e:
            _log.warning(f"intraday_5d daily 补拉失败: {e}")

    def _pool_one(d: str):
        d_compact = d.replace("-", "")
        try:
            return d, msf.fetch_zt_pool(d_compact) or []
        except Exception as e:
            _log.warning(f"涨停池拉取失败 {d}: {e}")
            return d, []

    seal_by_date: Dict[str, Dict] = {}
    try:
        with ThreadPoolExecutor(max_workers=len(recent5) or 1) as _p:
            pool_map = dict(_p.map(_pool_one, recent5))
    except Exception as e:
        _log.warning(f"涨停池并行拉取异常: {e}")
        pool_map = {}

    for d in recent5:
        pool = pool_map.get(d, [])
        for s in pool:
            if s.get("code") == code:
                lo = float(s.get("limit_order_amount", 0) or 0)
                amt = float(s.get("amount", 0) or 0)
                seal_by_date[d] = {
                    "was_limit_up": True,
                    "streak": int(s.get("streak", 1) or 1),
                    "sealed_amount": lo,
                    "total_amount": amt,
                    "seal_ratio_pct": round(lo / amt * 100, 2) if amt > 0 else None,
                    "first_seal_time": s.get("first_time", ""),
                    "burst_count": int(s.get("burst_count", 0) or 0),
                    "sector": s.get("sector", ""),
                }
                break

    return daily_dict, seal_by_date


def aggregate_5d_summary(out: Dict, daily_dict: Dict[str, Dict],
                         seal_by_date: Dict[str, Dict], recent5: List[str]) -> None:
    """拼 5 日 daily_5d + summary_5d 写回 out (in-place).

    R2000.35 (2026-08-17): 当 d == today_str 且 daily_dict 无该日 K 线 (盘中/收盘后未写)
    → 兜底从 out['intraday_today']['ticks'] 推导 OHLC (open=首 tick price, close=尾 tick,
    high/low=全 tick 极值, volume/amount=全 tick 累加), 避免今日行 all-zero.
    """
    today_ticks: List[Dict] = []
    _it = out.get("intraday_today") or {}
    if isinstance(_it, dict) and _it.get("ticks"):
        today_ticks = _it.get("ticks") or []
    today_str: Optional[str] = None
    if isinstance(_it, dict):
        today_str = _it.get("date")

    # 兜底: 从 today_ticks 算 OHLC
    today_open = today_high = today_low = today_close = 0.0
    today_vol_hand = 0.0
    today_amt = 0.0
    if today_ticks:
        _prices = [float(t.get("price") or 0) for t in today_ticks if t.get("price")]
        if _prices:
            today_open = _prices[0]
            today_close = _prices[-1]
            today_high = max(_prices)
            today_low = min(_prices)
        for t in today_ticks:
            today_vol_hand += float(t.get("volume_hand") or 0)
            _amt = float(t.get("amount") or 0)
            if _amt <= 0:
                # R2000.47 (2026-08-17): akshare tick 缺 amount — 用 price*volume_hand*100 兜底
                # (1 手 = 100 股, amount 元 = price × 手数 × 100). tencent tick 自带 amount 不进此分支.
                _px = float(t.get("price") or 0)
                _vh = float(t.get("volume_hand") or 0)
                _amt = _px * _vh * 100 if _px > 0 and _vh > 0 else 0
            today_amt += _amt

    prev_close = None
    daily_rows: List[Dict] = []
    # R2000.42 (2026-08-17): daily_dict 里有历史日全 0 的脏数据 (cache 未刷新),
    #   兜底 prev_close 用 intraday_today.prev_close (今日行的相对涨跌基准).
    it_prev_close = float((_it or {}).get("prev_close") or 0) if isinstance(_it, dict) else 0
    for d in recent5:  # 老→新 (recent5 已是升序)
        r = daily_dict.get(d, {})
        close = float(r.get("收盘", 0) or 0)
        # R2000.35: 今日行 daily_dict 无 K 线 (盘中/刚收盘) → 用 intraday_today.ticks 兜底
        if (not r) and (d == today_str) and today_ticks:
            open_v = today_open
            high_v = today_high
            low_v = today_low
            close_v = today_close
            volume_v = today_vol_hand  # 手数
            amount_v = today_amt
        else:
            open_v = float(r.get("开盘", 0) or 0)
            high_v = float(r.get("最高", 0) or 0)
            low_v = float(r.get("最低", 0) or 0)
            close_v = close
            volume_v = float(r.get("成交量", 0) or 0)
            amount_v = float(r.get("成交额", 0) or 0)
        # R2000.42: 当今日行 (d == today_str) 时, 优先用 intraday_today.prev_close
        #   (fetch_realtime 昨收) — 比循环 carry-forward 的 prev_close 更准.
        #   当 prev_close 为 0/None (历史行脏数据) 也兜底用.
        if d == today_str and it_prev_close and it_prev_close > 0:
            effective_prev = it_prev_close
        else:
            effective_prev = prev_close
        change_pct = ((close_v / effective_prev - 1) * 100) if (effective_prev and close_v) else None
        seal = seal_by_date.get(d, {})
        daily_rows.append({
            "date": d,
            "open": open_v,
            "high": high_v,
            "low": low_v,
            "close": close_v,
            "volume": volume_v,
            "amount": amount_v,
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "was_limit_up": seal.get("was_limit_up", False),
            "streak": seal.get("streak"),
            "sealed_amount": seal.get("sealed_amount"),
            "seal_ratio_pct": seal.get("seal_ratio_pct"),
            "first_seal_time": seal.get("first_seal_time", ""),
            "burst_count": seal.get("burst_count"),
            "sector": seal.get("sector", ""),
        })
        prev_close = close_v
    daily_rows.reverse()  # 新→老
    out["daily_5d"] = daily_rows

    rows_5 = daily_rows
    if rows_5:
        valid_changes = [r["change_pct"] for r in rows_5 if r.get("change_pct") is not None]
        closes_5 = [r["close"] for r in rows_5 if r["close"]]
        seal_ratios = [r["seal_ratio_pct"] for r in rows_5 if r.get("seal_ratio_pct") is not None]
        streaks = [r["streak"] for r in rows_5 if r.get("was_limit_up") and r.get("streak")]
        up_days = sum(1 for c in valid_changes if c > 0)
        lu_days = sum(1 for r in rows_5 if r.get("was_limit_up"))
        # R2000.36 (2026-08-17): cum_pct 之前用 (oldest/newest - 1) 是反向的.
        #   daily_rows 已 reverse 成 [newest, ..., oldest], closes_5[0] 是最新,
        #   closes_5[-1] 是最旧. 应该是 (newest/oldest - 1) * 100 = (closes_5[0]/closes_5[-1] - 1) * 100
        cum_pct = round((closes_5[0] / closes_5[-1] - 1) * 100, 2) if len(closes_5) >= 2 and closes_5[-1] else None
        out["summary_5d"] = {
            "cum_pct":        cum_pct,
            "up_days":        up_days,
            "down_days":      len(valid_changes) - up_days,
            "limit_up_days":  lu_days,
            "avg_change_pct": round(sum(valid_changes) / len(valid_changes), 2) if valid_changes else None,
            "max_change_pct": round(max(valid_changes), 2) if valid_changes else None,
            "min_change_pct": round(min(valid_changes), 2) if valid_changes else None,
            "avg_seal_ratio": round(sum(seal_ratios) / len(seal_ratios), 2) if seal_ratios else None,
            "max_streak":     max(streaks) if streaks else 0,
            "high_5d":        round(max([r["high"] for r in rows_5 if r["high"]]), 2) if rows_5 and any(r["high"] for r in rows_5) else None,
            "low_5d":         round(min([r["low"]  for r in rows_5 if r["low"]]),  2) if rows_5 and any(r["low"]  for r in rows_5) else None,
        }


def load_today_ticks(code: str, today_str: str, out: Dict,
                     _safe_float, _filter_intraday_ticks_for_date,
                     _fetch_intraday_today_tencent_first) -> None:
    """今日分时 tick 加载 (akshare 主 + tencent 兜底 + 日期防御), 写回 out (in-place).

    依赖 3 个外部函数由 caller 注入, 避免循环 import:
      _safe_float                  — lib_common._safe_float
      _filter_intraday_ticks_for_date — server 端已有的清洗函数
      _fetch_intraday_today_tencent_first — server 端兜底 fetch
    """
    try:
        import akshare as ak
        # R-PERF-066 (2026-08-22): akshare 无内建 timeout — sandbox DNS 偶发 hang 5-15s.
        #   用 Thread + join timeout 7s 强制 bound, 失败 → 走 tencent 兜底 (line 305).
        #   实测 v7 bench 600519/intraday_5d P95=12s 都是这一行 hang. 7s 留余量给
        #   正常 akshare 1-3s 响应, 超时立即放弃.
        import threading as _thr
        _ak_box = {}
        def _ak_run():
            try:
                _ak_box["df"] = ak.stock_intraday_em(symbol=code)
            except Exception as _e:
                _ak_box["err"] = _e
        _ak_t = _thr.Thread(target=_ak_run, daemon=True)
        _ak_t.start()
        _ak_t.join(timeout=7.0)
        if _ak_t.is_alive():
            _log.warning(f"akshare 今日分时 tick 超时 7s (sandbox DNS hang?), 走 tencent 兜底")
            df = None
        else:
            df = _ak_box.get("df")
            if "err" in _ak_box:
                raise _ak_box["err"]
        if df is not None and not df.empty:
            ticks = []
            for _, r in df.iterrows():
                ticks.append({
                    "time": str(r.get("时间", "")),
                    "price": _safe_float(r.get("成交价")),
                    "volume_hand": _safe_float(r.get("手数")),
                    "side": str(r.get("买卖盘性质", "")),
                })
            out["intraday_today"] = {
                "date": today_str,
                "ticks": ticks,
                "ticks_n": len(ticks),
                "source": "akshare",
            }
    except Exception as e:
        _log.warning(f"akshare 今日分时 tick 拉取失败: {e}")

    # 日期防御 — 多源并行无日期校验时,tick 可能混入昨日/前日 1min 数据
    if out.get("intraday_today") and out["intraday_today"].get("ticks"):
        _src = out["intraday_today"].get("source", "")
        _allow_t = _src in ("akshare", "akshare_intraday_em", "tencent_1min", "tencent_intraday")
        _ticks_clean, _dates = _filter_intraday_ticks_for_date(
            out["intraday_today"]["ticks"], today_str.replace("-", ""),
            allow_time_only=_allow_t,
        )
        if len(_ticks_clean) < len(out["intraday_today"]["ticks"]):
            _log.warning(
                f"intraday_5d {code} today tick 过滤 {len(out['intraday_today']['ticks'])}→{len(_ticks_clean)} "
                f"(dates={sorted(_dates)}, src={_src})"
            )
        out["intraday_today"]["ticks"] = _ticks_clean
        out["intraday_today"]["ticks_n"] = len(_ticks_clean)

    # akshare 失败 → tencent 1min 兜底 (沙箱 DNS 劫持环境必须)
    if not out.get("intraday_today") or not out["intraday_today"].get("ticks"):
        ten = _fetch_intraday_today_tencent_first(code)
        if ten and ten.get("ticks"):
            out["intraday_today"] = ten
        else:
            out["note"] = "今日分时未取到(akshare 断连, tencent 兜底失败)"


def stage_cache_write(cache_key_today: str, cache_key_hist: str,
                      out: Dict, fresh: int, _store_set_fn) -> None:
    """R51 (Batch 6) 双段缓存 stage 化 — 任一阶段成功就写 L1, 超时兜底可用."""
    # hist 段 (去掉 today 字段)
    hist_only = {k: v for k, v in out.items() if k != "intraday_today"}
    if not fresh and (hist_only.get("daily_5d") or hist_only.get("intraday_per_day")):
        # R2000.35: 防御 — 全 0 daily_5d 不写回 Redis (上游断连/缓存过期时, 旧数据更可靠)
        # 检查至少 1 行有非零 close OR 显式今天行用了 intraday 兜底
        _daily_5d = hist_only.get("daily_5d") or []
        _hist_rows_with_close = [r for r in _daily_5d if (r.get("close") or 0) > 0]
        if _daily_5d and not _hist_rows_with_close:
            _log.warning(f"intraday_5d {hist_only.get('code') or out.get('code')} 5 行 daily_5d 全 0 close, 跳过 hist 缓存写入")
        else:
            _store_set_fn(cache_key_hist, hist_only, ttl=1800)
    # today 段
    if not fresh and out.get("intraday_today") and out["intraday_today"].get("ticks"):
        _store_set_fn(cache_key_today, {"intraday_today": out["intraday_today"]}, ttl=60)