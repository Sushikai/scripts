"""
实时推票引擎 (R2001.2) — 同步版,直接遍历 universe dict。

R2004.1 (2026-08-19) 数据准确性重构:
  - _get_universe 补全真实字段: mcap_yi / change_pct(spot) / 真实量比 / 日线特征
    (上影线比例 / 开盘涨幅 / 横盘天数 / 距涨停天数 / 未破涨停底价)
  - 修 log NameError
  - _rule_hits: BV05 补完整语义, BV11/12 用开盘涨幅且排除今日涨停股,
    first_5min_vol_ratio 无真实数据时不再 fallback 到 vol_ratio

后续 round:
  R2002.1 加 zt_screener 复用 (cross-link, 涨停溢价也命中)
  R2002.2 加 dexin_screener 复用
  R2002.3 加 sector 行业筛选
"""
import logging
import time

from .rules import compute_score, load_rules

log = logging.getLogger("tuixue.bv.screener")


# L0 in-proc 缓存
_LIVE_CACHE: dict = {"data": None, "ts": 0.0}
_LIVE_TTL = 30.0  # 跟 zt_screener 一致

# R248: 板块涨幅映射缓存 (5min TTL) — THS 全行业 summary 低频上下文, 不阻塞 30s 推票路径
_SECTOR_PCT: dict = {"map": None, "ts": 0.0}
_SECTOR_PCT_TTL = 300.0
# R249: 东财二级行业名罗马数字后缀归一化 — stock_zt_pool_em 所属行业列用 中药Ⅱ/电机Ⅱ/综合Ⅱ
#   (二级行业标注), THS summary 用标准名 中药/电机/综合. strip 后缀 → 同一实体对齐查表.
#   保留原名字段给前端展示 (前端数据来自 pick.sector, 此处只归一化查表键).
_ROMAN_SUFFIX_RE = None


def _normalize_sector_key(name: str) -> str:
    """R249: 板块名归一化为查表键 — strip 东财罗马数字二级行业后缀.

    第一性原理: 中药Ⅱ 与 中药 是同一实体的不同字形 (东财二级标注 vs THS 标准名),
    查表 miss 导致板块涨幅信号静默丢失. 归一到标准名才能命中. 非罗马数字名原样返回.
    """
    if not name:
        return name
    global _ROMAN_SUFFIX_RE
    if _ROMAN_SUFFIX_RE is None:
        import re
        _ROMAN_SUFFIX_RE = re.compile(r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+$")
    s = name.strip()
    m = _ROMAN_SUFFIX_RE.search(s)
    if m:
        s = s[: m.start()]
    return s.strip()


def _sector_pct_map() -> dict[str, float]:
    """THS 全行业板块涨幅 → {板块名: change_pct} (R248)。

    第一性原理: pick 的 sector 字段是 THS 行业名 (化学制药/生物制品...), 和
    sectors_full 同源 (stock_board_industry_summary_ths, 90 行业). 板块涨幅是
    板块热度的温度计 — "为什么推这只" 的上下文. 零额外请求: 5min 模块缓存.
    """
    now = time.time()
    if _SECTOR_PCT["map"] is not None and now - _SECTOR_PCT["ts"] < _SECTOR_PCT_TTL:
        return _SECTOR_PCT["map"]
    try:
        from ... import multi_source_fetchers as msf
        m: dict[str, float] = msf.fetch_all_sector_pct() or {}
        # R249: 键归一化 (strip 罗马数字后缀) — THS 标准名进 map, 查表时也用归一化键
        norm = {}
        for k, v in m.items():
            norm[_normalize_sector_key(k)] = v
        _SECTOR_PCT["map"] = norm
        _SECTOR_PCT["ts"] = now
        return norm
    except Exception as e:
        log.debug(f"bv sector_pct_map 失败: {e}")
        return _SECTOR_PCT["map"] or {}


def _daily_feature(code: str) -> tuple[str, dict | None]:
    """本地日线缓存 → 每股独有技术特征 (零网络)。

    返回:
      upper_shadow_ratio  — 上影线 / 收盘价 (今日K线)
      open_gap_pct        — 今开 / 昨收 - 1, ×100 (%)
      consolidation_days  — 最近连续横盘天数 (高低点 ±8% 区间)
      streak_days_ago     — 距最近一次涨停的天数 (99=60日内无涨停)
      above_streak_floor  — 现价是否 ≥ 涨停日收盘价 × 0.99 (未破涨停底价)
    """
    try:
        from ... import cache_db as _cdb
        df = _cdb.daily().get(code, 60)
        if df is None or df.empty or len(df) < 6:
            return code, None
        for c in ("开盘", "最高", "最低", "收盘"):
            if c not in df.columns:
                return code, None
        closes = df["收盘"].astype(float)
        close = float(closes.iloc[-1])
        prev_close = float(closes.iloc[-2])
        op = float(df["开盘"].iloc[-1])
        high = float(df["最高"].iloc[-1])
        low = float(df["最低"].iloc[-1])
        if close <= 0 or prev_close <= 0:
            return code, None

        upper_shadow = max(high - max(op, close), 0.0) / close
        open_gap = (op - prev_close) / prev_close

        # 横盘天数: 最近连续 N 根K线高低点都在 [ref*0.92, ref*1.08] 内
        highs = df["最高"].astype(float).tolist()
        lows = df["最低"].astype(float).tolist()
        ref = (highs[-1] + lows[-1]) / 2.0
        lo_b, hi_b = ref * 0.92, ref * 1.08
        consolidation = 0
        for i in range(len(highs) - 1, -1, -1):
            if lo_b <= lows[i] and highs[i] <= hi_b:
                consolidation += 1
            else:
                break

        # 距最近涨停天数 + 未破涨停底价 (回溯 60 日; 创板/科创 20%, 主板 10%)
        thr = 19.5 if code.startswith(("300", "301", "688", "689")) else 9.5
        streak_days_ago = 99
        above_streak_floor = False
        for i in range(len(closes) - 2, -1, -1):
            chg = (closes.iloc[i + 1] - closes.iloc[i]) / closes.iloc[i] * 100.0
            if chg >= thr:
                limit_close = float(closes.iloc[i + 1])
                streak_days_ago = len(closes) - 2 - i  # 涨停日距今天数
                above_streak_floor = close >= limit_close * 0.99
                break

        return code, {
            "upper_shadow_ratio": round(max(upper_shadow, 0.0), 4),
            "open_gap_pct": round(open_gap * 100.0, 2),
            "consolidation_days": consolidation,
            "streak_days_ago": streak_days_ago,
            "above_streak_floor": above_streak_floor,
        }
    except Exception as e:
        log.debug(f"bv daily_feature {code} 失败: {e}")
        return code, None


def _batch_daily_feature(codes: list[str]) -> dict[str, dict]:
    """并行日线特征 — 纯本地 SQLite/Redis, ~80 只 200ms 内。"""
    if not codes:
        return {}
    from concurrent.futures import ThreadPoolExecutor
    out: dict[str, dict] = {}
    ex = ThreadPoolExecutor(max_workers=min(12, max(1, len(codes))))
    try:
        futs = [ex.submit(_daily_feature, c) for c in codes]
        for fut in futs:
            try:
                code, data = fut.result(timeout=5)
                if data:
                    out[code] = data
            except Exception:
                continue
    finally:
        ex.shutdown(wait=True)
    return out


# 日线缓存后台预热 — 全局单飞 (多 worker 下各自进程内, 幂等)
_daily_warmup_state = {"inflight": False, "ts": 0.0}


def _warmup_daily_async(codes: list[str]) -> None:
    """后台单飞预热日线缓存 — 不阻塞当前请求, 5min 冷却。

    用于近期涨停股 (非今日) 的日线特征补齐 — BV04/BV05/BV11/BV12 依赖
    upper_shadow/consolidation/streak_days_ago 等字段, 而这些字段需要日线数据。
    """
    if not codes:
        return
    now = time.time()
    if _daily_warmup_state["inflight"] or (now - _daily_warmup_state["ts"]) < 300:
        return
    _daily_warmup_state["inflight"] = True

    def _run():
        try:
            from concurrent.futures import ThreadPoolExecutor
            from ... import lib_common as _lc
            from ... import cache_db as _cdb
            db = _cdb.daily()
            ex = ThreadPoolExecutor(max_workers=8)

            def _one(c):
                try:
                    df = _lc.fetch_daily(c, days=60)
                    if df is not None and not df.empty:
                        db.set(c, df)
                except Exception:
                    pass

            futs = [ex.submit(_one, c) for c in codes]
            for f in futs:
                try:
                    f.result(timeout=12)
                except Exception:
                    continue
            ex.shutdown(wait=True)
        except Exception as e:
            log.debug(f"bv daily warmup 失败: {e}")
        finally:
            _daily_warmup_state["inflight"] = False
            _daily_warmup_state["ts"] = time.time()

    import threading
    threading.Thread(target=_run, daemon=True, name="bv-daily-warmup").start()


def _get_universe() -> dict:
    """拉全市场报价快照 — 复用 all_stocks._UNIVERSE_CACHE (canonical 5min 缓存)。

    R-fix 2026-08-19: 修复所有股票 score 都是 25.8 的 bug —
      原 _build_universe 走 _fetch_today_zt (data_layer.fetch_limit_up_pool) 只输出 3 字段
      (name, zt_today, zt_recent), 后面 merge 用 msf.fetch_zt_pool 期望 streak/turnover/limit_amount
      但 base 里这些字段都是 None, 全部 79 只 zt 都进 streak=1 兜底 → 命中同一组规则 → 同样 score
      修复: 直接用 msf.fetch_zt_pool + msf.fetch_recent_zt_pool 构造 universe, 每只 zt 都有完整字段

    R2004.1 (2026-08-19) 数据准确性重构:
      之前 change_pct 硬编码 10.0 / mcap_yi 恒 0 / 无日线特征 → 7 条规则里
      BV04/BV05/BV11/BV12 永远不命中, 只剩 BV03 人人命中 → 命中规则全部相同。
      现在:
        - spot 快照补真实 change_pct / 量比 / 换手 / 总市值
        - zt_pool 补流通市值 mcap_yi
        - 涨停池 + 近期涨停股补日线特征 (上影线/开盘涨幅/横盘/距涨停/涨停底价)
    """
    from ... import multi_source_fetchers as msf
    from ... import data_layer as _dl
    from datetime import datetime as _dt

    base: dict[str, dict] = {}
    today = _dt.now().strftime("%Y%m%d")

    # 1) 全部代码 + 名称 (兜底)
    try:
        all_codes = _dl.fetch_stock_list_all() or []
        name_map = {c: n for c, n in all_codes}
    except Exception:
        name_map = {}
    for c, n in name_map.items():
        base[c] = {"name": n, "zt_today": False, "zt_recent": 0}

    # 2) 涨停池 (用 msf.fetch_zt_pool, 返 streak/turnover/limit_order_amount/first_time/burst_count/sector)
    pool: list[dict] = []
    try:
        pool = msf.fetch_zt_pool(today) or []
    except Exception as e:
        log.debug(f"bv fetch_zt_pool 失败: {e}")

    # 3) spot 全市场快照 (真实涨幅 / 量比 / 换手 / 市值) — 6s 超时
    spot: dict[str, dict] = {}
    try:
        spot = msf.fetch_spot_a_full(overall_timeout=6) or {}
    except Exception as e:
        log.debug(f"bv fetch_spot_a_full 失败: {e}")

    zt_codes: list[str] = []
    for r in pool:
        code = str(r.get("code", "")).zfill(6)
        if not code or code not in base:
            continue
        zt_codes.append(code)
        sp = spot.get(code, {}) or {}
        amt = float(r.get("amount", 0) or 0)
        mcap_float = float(r.get("market_cap", 0) or 0) / 1e8
        if mcap_float <= 0:
            mcap_float = float(sp.get("总市值", 0) or 0) / 1e8
        streak_v = int(r.get("streak", 0) or 0)
        base[code].update({
            "name": r.get("name", "") or base[code].get("name", ""),
            "zt_today": True,
            "streak": streak_v,
            "turnover_pct": float(sp.get("换手率", 0) or r.get("turnover_pct", 0) or 0),
            "amount": amt,
            "amount_yi": round(amt / 1e8, 2),
            "limit_order_amount": float(r.get("limit_order_amount", 0) or 0),
            "first_time": str(r.get("first_time", "") or ""),
            "burst_count": int(r.get("burst_count", 0) or 0),
            "sector": str(r.get("sector", "") or ""),
            "mcap_yi": round(mcap_float, 2),
            # R2004.1: 真实涨幅替代硬编码 10.0 (涨停股≈10/20, 开板股真实值)
            "change_pct": float(sp.get("涨跌幅", 0) or 0),
        })
        # R2004.1: 真实量比优先, 换手率/10 兜底 (涨停池无量比字段)
        vol_ratio = float(sp.get("量比", 0) or 0)
        if vol_ratio <= 0:
            vol_ratio = max(float(r.get("turnover_pct", 0) or 0) / 10.0, 1.0)
        base[code]["vol_ratio"] = vol_ratio
        base[code]["volume_ratio"] = vol_ratio
        if amt > 0:
            base[code]["seal_ratio"] = round(base[code]["limit_order_amount"] / amt, 3)
        else:
            base[code]["seal_ratio"] = 0.0

    # 4) 近期涨停 (补 zt_recent, 进入日线特征集合)
    recent_zt_codes: list[str] = []
    try:
        recent = msf.fetch_recent_zt_pool(days=3) or {}
        for c, info in recent.items():
            if c in base and not base[c].get("zt_today"):
                base[c]["zt_recent"] = int(info.get("zt_count", 0) or 0)
                recent_zt_codes.append(c)
                # R2004.1: 补 spot 真实字段 (近期涨停非今日 — 真实涨幅/量比/换手/市值)
                sp = spot.get(c, {}) or {}
                if sp:
                    chg = float(sp.get("涨跌幅", 0) or 0)
                    if chg:
                        base[c]["change_pct"] = chg
                    vr = float(sp.get("量比", 0) or 0)
                    if vr > 0:
                        base[c]["vol_ratio"] = vr
                    to = float(sp.get("换手率", 0) or 0)
                    if to > 0:
                        base[c]["turnover_pct"] = to
                    mv = float(sp.get("总市值", 0) or 0) / 1e8
                    if mv > 0:
                        base[c]["mcap_yi"] = round(mv, 2)
    except Exception as e:
        log.debug(f"bv fetch_recent_zt_pool 失败: {e}")

    # 5) 日线特征 (涨停池 + 近期涨停, 并行) — 决定 BV04/BV05/BV11/BV12
    feat_codes = zt_codes + [c for c in recent_zt_codes if c not in zt_codes]
    if feat_codes:
        try:
            feats = _batch_daily_feature(feat_codes)
            for code, ft in feats.items():
                if ft:
                    base[code].update(ft)
            # 今日涨停股: 日线缓存 miss 时用 zt_pool/spot 合成 — 数据准确 (今日收盘=涨停价):
            #   未破涨停底价 / 距涨停 0 天 / 封板无上影 / 开盘涨幅兜底当日涨幅
            #   注: BV11/BV12 已排除今日涨停股, 故 open_gap_pct 兜底不影响规则判定
            for c in zt_codes:
                if c not in feats and c in base:
                    base[c]["above_streak_floor"] = True
                    base[c]["streak_days_ago"] = 0
                    base[c]["open_gap_pct"] = float(base[c].get("change_pct", 0) or 0)
                    base[c]["upper_shadow_ratio"] = 0.0
                    base[c]["consolidation_days"] = 0
            # 缓存 miss 的股票 → 后台单飞预热日线 (不阻塞当前请求)
            miss = [c for c in feat_codes if c not in feats]
            if miss:
                _warmup_daily_async(miss)
        except Exception as e:
            log.debug(f"bv daily_feature 批量失败: {e}")

    return base



def _synth_universe_from_universes() -> dict:
    """兜底 — 试 all_stocks + dragon 池拼一个最小 universe (R2001.6 临时代替)。"""
    out = {}
    try:
        from .. import all_stocks
        if hasattr(all_stocks, "_CACHE"):
            for code, info in all_stocks._CACHE.items():
                if isinstance(info, dict):
                    out[code] = info
    except Exception:
        pass
    return out


# R-2026-08-20: 板块分类 — 创板/科创 (300/301/688/689) 为 20cm, 主板为 10cm
_BOARD_CODES_20CM = {"300", "301", "688", "689"}


def _is_20cm(code: str) -> bool:
    """代码前缀判 20cm (创业板/科创板), 否则 10cm (主板)。"""
    return code[:3] in _BOARD_CODES_20CM


def _rule_hits(stock_info: dict) -> list[str]:
    """命中规则: BV03/BV04/BV05/BV06/BV07/BV11/BV12 (R2003.5 增至 7 条, BV07 真实条件)。

    注: bv_rules.json 已有 BV06/07/13 但语义不同 — 复用 BV06 强封单语义 + BV07 早盘前 10:40

    R2004.1 数据准确性:
      - BV05 补全语义: 距涨停 ≤15 天 + 未破涨停底价 (原只剩 streak+mcap → 大量误命中)
      - BV11/BV12 用开盘涨幅 open_gap_pct (fallback 当日涨幅), 且今日涨停股排除
        (涨停 ≠ 滞涨, 卖出纪律规则不适用于仍封板的票)
      - first_5min_vol_ratio 无真实数据时默认 0 (不再 fallback vol_ratio, 避免 BV12 误命中)
    """
    matched: list[str] = []
    streak = int(stock_info.get("streak", 0) or 0)
    if streak == 0:
        zt_recent = int(stock_info.get("zt_recent", 0) or 0)
        if zt_recent > 0:
            streak = zt_recent
        elif stock_info.get("zt_today"):
            streak = 1
    vol_ratio = float(stock_info.get("vol_ratio", 1.0) or 1.0)
    volume_ratio = float(stock_info.get("volume_ratio", vol_ratio) or vol_ratio)
    mcap = float(stock_info.get("mcap_yi", 0) or 0)
    # R2004.1: 开盘涨幅优先 (日线真实), fallback 当日涨幅;
    #   两者都缺失 = 无真实行情数据 → BV11/BV12 不参与判定 (原 `or 0` 让 0 落入
    #   BV11 区间 [-0.5, 1.0] → 全市场误命中)
    open_gap_raw = stock_info.get("open_gap_pct")
    if open_gap_raw is None:
        open_gap_raw = stock_info.get("change_pct")
    open_gap = float(open_gap_raw) if open_gap_raw is not None else None
    upper_shadow = float(stock_info.get("upper_shadow_ratio", 0) or 0)
    consolidation = int(stock_info.get("consolidation_days", 0) or 0)
    # R2004.1: 无真实分时数据时默认 0, 不参与 BV12 判定
    first_5min_vol = float(stock_info.get("first_5min_vol_ratio", 0) or 0)
    turnover = float(stock_info.get("turnover_pct", 0) or 0)
    seal_ratio = float(stock_info.get("seal_ratio", 0) or 0)
    burst_count = int(stock_info.get("burst_count", 0) or 0)
    first_time = str(stock_info.get("first_time", "") or "")
    # BV03: 有异动 — vol_ratio >= 1.5 或 streak >= 1
    if vol_ratio >= 1.5 or streak >= 1:
        matched.append("BV03")
    # BV04: 放量长上影 — 上影 >= 3% + vol_ratio >= 2 + 横盘 >= 30d
    if upper_shadow >= 0.03 and vol_ratio >= 2.0 and consolidation >= 30:
        matched.append("BV04")
    # BV05: 一进二 — streak 1-2 + mcap <= 100 亿 + 距涨停 ≤15d + 未破涨停底价
    if 1 <= streak <= 2 and 0 < mcap <= 100:
        if int(stock_info.get("streak_days_ago", 99)) <= 15 and stock_info.get("above_streak_floor", False):
            matched.append("BV05")
    # BV06: 强封单 (封成比 ≥ 15%) — R2003.5 利用 seal_ratio 字段
    if seal_ratio >= 0.15 and streak >= 1:
        matched.append("BV06")
    # BV07: 早盘封板 (first_time < 10:40) — 复用规则,与轮动窗口配合
    if first_time:
        try:
            ft = str(first_time).strip()
            if ":" in ft:
                # 09:25:00 格式
                parts = ft.split(":")
                hh, mm = int(parts[0]), int(parts[1])
            elif len(ft) >= 4 and ft[:4].isdigit():
                # 092500 格式 (HHMMSS)
                hh = int(ft[:2])
                mm = int(ft[2:4])
            else:
                hh, mm = None, None
            if hh is not None:
                minutes = hh * 60 + mm
                if minutes <= 10 * 60 + 40 and streak >= 1:
                    matched.append("BV07")
        except Exception:
            pass
    # R2004.1: BV11/BV12 是卖出纪律 — 今日涨停股不适用 (涨停≠滞涨), 且需真实行情数据
    if open_gap is not None and not stock_info.get("zt_today"):
        # BV11: 上冲不破均价线 — 涨幅 -0.5%~1% + 缩量
        if -0.5 <= open_gap <= 1.0 and 0.5 <= vol_ratio <= 1.0:
            matched.append("BV11")
        # BV12: 放量滞涨 — 涨幅 1%-3% + 量比 >= 1.5
        if 1 <= open_gap <= 3 and (vol_ratio >= 1.5 or first_5min_vol >= 1.5):
            matched.append("BV12")
    return matched






def screen_universe(top_n: int = 15) -> dict:
    """扫一遍实时 universe, 找出命中多条规则的标的。

    数据源:
      - _realtime_poller._UNIVERSE 预热的全市场报价

    返回:
      {
        "ts": float, "scanned": int, "matched": int, "top_n": int,
        "picks": [{code, name, sector, change_pct, amount_yi, vol_ratio, streak,
                   score, matched_rules, rule_count, weighted_sum, top_rule}],
        "phase": str, "phase_label": str, "phase_ttl": int,
      }
    """
    from .realtime import phase_meta

    universe = _get_universe() or _synth_universe_from_universes()
    scanned = len(universe)
    # R248: 板块涨幅映射 (THS 全行业, 5min 模块缓存) — 填 sector_change_pct 信号
    sector_pct = _sector_pct_map()
    picks: list[dict] = []
    rules_by_id = {r["id"]: r for r in load_rules().get("rules", [])}

    for code, info in universe.items():
        if not isinstance(info, dict):
            continue
        matched = _rule_hits(info)
        if not matched:
            continue
        score, count, weighted = compute_score(matched, stock_info=info)
        top_rid = matched[0]
        top_rule = rules_by_id.get(top_rid, {})
        # R67: score_breakdown — 每条命中规则贡献分 (加权占比, 前端 R63 依赖它排序/画权重条)
        breakdown = []
        if weighted > 0:
            for rid in matched:
                w = rules_by_id.get(rid, {}).get("score_weight", 0) or 0
                breakdown.append({
                    "rule_id": rid,
                    "contribution": round(w / weighted * score, 1),
                    "weight": w,
                })
        change_pct = float(info.get("change_pct", 0) or 0)
        # R2004.1: amount_yi 已由 _get_universe 算好 (亿); 兜底兼容旧数据
        amount_raw = float(info.get("amount_yi", 0) or info.get("amount", 0) or 0)
        amount_yi = amount_raw / 1e8 if amount_raw > 1e6 else amount_raw
        # R2003.8: streak 输出也用 zt_recent/zt_today 兜底
        streak_out = int(info.get("streak", 0) or 0)
        if streak_out == 0:
            zt_recent = int(info.get("zt_recent", 0) or 0)
            if zt_recent > 0:
                streak_out = zt_recent
            elif info.get("zt_today"):
                streak_out = 1
        # R-2026-08-20: 优先推 10cm — 标记板块, 排序时 10cm 靠前 (同分时 10cm 优先)
        is_20cm = _is_20cm(code)
        sector_name = info.get("sector", "") or info.get("industry", "")
        # R248: 板块涨幅 — THS 行业名对齐 (pick sector 与 sectors_full 同源),
        #   查表填, miss (如 "中药Ⅱ" 罗马数字变体) 返回 None 前端不渲染.
        # R249: 查表键归一化 (strip 东财罗马数字后缀) — 中药Ⅱ → 中药 命中标准名.
        sector_change = sector_pct.get(_normalize_sector_key(sector_name)) if sector_name else None
        picks.append({
            "code": code,
            "name": info.get("name", ""),
            "is_20cm": is_20cm,
            "board": "20cm" if is_20cm else "10cm",
            "sector": sector_name,
            "sector_change_pct": round(sector_change, 2) if sector_change is not None else None,
            "change_pct": round(change_pct, 2),
            "amount_yi": round(amount_yi, 2),
            "vol_ratio": round(float(info.get("vol_ratio", 1.0) or 1.0), 2),
            "streak": streak_out,
            "first_time": str(info.get("first_time", "") or ""),
            "seal_ratio": round(float(info.get("seal_ratio", 0) or 0), 3),
            "burst_count": int(info.get("burst_count", 0) or 0),
            "turnover_pct": round(float(info.get("turnover_pct", 0) or 0), 2),
            "mcap_yi": round(float(info.get("mcap_yi", 0) or 0), 2),
            "score": score,
            "matched_rules": matched,
            "rule_count": count,
            "weighted_sum": weighted,
            "score_breakdown": breakdown,
            "top_rule": {
                "id": top_rid,
                "title": top_rule.get("title", ""),
                "quote": top_rule.get("quote", ""),
                # R67: 补 timestamp — 前端 R65 时间戳锚点依赖它跳视频
                "timestamp": top_rule.get("timestamp", ""),
            },
        })

    # R-2026-08-20: 优先推 10cm — 同分时 10cm (board_pri=0) 整体排在 20cm (board_pri=1) 前
    #   是次级排序权重 (在 score 之后, weighted_sum/change_pct 之前),
    #   仍保留 score/weighted/change 排序权重, 20cm 票不会消失, 仅为同分时降权
    picks.sort(key=lambda x: (-x["score"], int(x.get("is_20cm", False)),
                              -x["weighted_sum"], -x["change_pct"]))
    p_meta = phase_meta()
    return {
        "ts": time.time(),
        "scanned": scanned,
        "matched": len(picks),
        "top_n": top_n,
        "picks": picks[:top_n],
        "phase": p_meta["phase"],
        "phase_label": p_meta["label"],
        "phase_ttl": p_meta["ttl"],
        "phase_tone": p_meta["tone"],
        "phase_icon": p_meta["icon"],
    }


def live_pick_sync(top_n: int = 15, refresh: bool = False) -> dict:
    """同步入口 — 供 server.py 调用。L0 in-proc 缓存 30s。"""
    now = time.time()
    if not refresh and _LIVE_CACHE["data"] and (now - _LIVE_CACHE["ts"]) < _LIVE_TTL:
        out = dict(_LIVE_CACHE["data"])
        out["_cache_hit"] = "inproc"
        return out
    try:
        data = screen_universe(top_n=top_n)
    except Exception as e:
        return {
            "ts": now, "scanned": 0, "matched": 0, "top_n": top_n,
            "picks": [], "phase": "close",
            "_error": f"{type(e).__name__}: {e}",
        }
    _LIVE_CACHE["data"] = data
    _LIVE_CACHE["ts"] = now
    out = dict(data)
    out["_cache_hit"] = "fresh"
    return out