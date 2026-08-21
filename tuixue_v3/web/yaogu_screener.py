"""
web/yaogu_screener.py — 妖股页面后端 (YAOGU 500 调研 → 1000 迭代)

API:
  GET /api/yaogu/live      — 今日妖股榜单: 涨停池 → 妖性评分 → 阶段分类 → 抓取信号 + 断板预警
  GET /api/yaogu/backtest  — 妖股回测 (同 ZT 口径: T+1 开盘买 + 一字板空仓 + 0.66% 成本)
  GET /api/yaogu/params    — 当前评分参数

调研依据 (YAOGU_500_SURVEY.md):
  - 2板介入期望最优 (胜率42% avg+0.52%); 3板 avg 更高但空仓率 56%
  - 断板低吸负期望 (34% 胜率) → 不做抓取信号, 仅断板预警
  - 环境闸门: 涨停家数≥30 且 晋级率≥25% (6b.3 分位校准)
"""
from __future__ import annotations

import json
import logging
import time as systime
from datetime import datetime
from pathlib import Path

from fastapi import Request

from .. import multi_source_fetchers as msf
from .. import yaogu_backtest as ybt

log = logging.getLogger("tuixue_v3.yaogu")

# ═══════════════════════════════════════════
# 妖性评分权重 (满分 100, 调研文档 §7.2)
# 6 维原始 hard-code, 寻优结果覆盖在 /tmp/yaogu_weights.json
# 寻优时: _score_* 函数返回 raw (0-100), 总分处按 W_* / FALLBACK[dim] 缩放
# ═══════════════════════════════════════════
_FALLBACK_W = {"streak": 25, "turn": 20, "mcap": 15, "fund": 20, "topic": 10, "env": 10}
_WEIGHTS_PATH = Path("/tmp/yaogu_weights.json")
_WEIGHT_DIMS = ("streak", "turn", "mcap", "fund", "topic", "env")


def _load_weights() -> tuple[dict, dict]:
    """读 /tmp/yaogu_weights.json (存在且 sum=100 时), 否则用 _FALLBACK_W.
    返回 (weights, meta). weights = {streak: float, ...}, meta 含 source/optimized_at/score 等.
    """
    meta = {"source": "fallback", "optimized_at": None, "score": None,
            "in_sample_score": None, "out_of_sample_score": None, "overfit_gap_pct": None,
            "iterations": None, "note": None}
    if not _WEIGHTS_PATH.exists():
        return _FALLBACK_W.copy(), meta
    try:
        data = json.loads(_WEIGHTS_PATH.read_text())
        w = data.get("weights") or {}
        if not all(k in w for k in _WEIGHT_DIMS):
            raise ValueError(f"missing dims: {set(_WEIGHT_DIMS) - set(w.keys())}")
        total = sum(w.values())
        if abs(total - 100) > 0.5:
            raise ValueError(f"sum != 100: {total}")
        meta.update({k: data.get(k) for k in meta.keys() if data.get(k) is not None})
        meta["source"] = "optimized"
        return w, meta
    except Exception as e:
        log.warning("读 %s 失败: %s, 用 fallback", _WEIGHTS_PATH, e)
        return _FALLBACK_W.copy(), meta


def _score_streak(streak: int, max_w: float | None = None) -> float:
    """连板高度: 2板=30%→7.5, 3板=45%→11.25, 4板=60%→15, 5板=75%→18.75, 6+=85%→21.25 (raw 0-100, 总分处按 max_w 缩放)"""
    base = {1: 0, 2: 30, 3: 45, 4: 60, 5: 75}.get(streak, 85)
    return float(base)


def _score_turn(turnover_pct: float, max_w: float | None = None) -> float:
    """量能活跃 (raw 0-100): 调研成妖期日换手 10-30% 最佳 (高位换筹 20-65%); 低换手=一字板买不进"""
    if turnover_pct <= 0:
        return 10.0
    if turnover_pct < 3:
        return 20.0     # 一字/缩量板, 买不进
    if turnover_pct < 10:
        return 50.0     # 温和
    if turnover_pct <= 30:
        return 100.0    # 黄金区间
    if turnover_pct <= 50:
        return 70.0     # 高换手但仍在可接受
    return 40.0         # >50% 过度换手, 筹码松动


def _score_mcap(mcap_yi: float, max_w: float | None = None) -> float:
    """市值弹性 (raw 0-100): 调研启动 <100亿, 50亿最密集"""
    if mcap_yi <= 0:
        return 33.3
    if mcap_yi < 60:
        return 100.0
    if mcap_yi < 100:
        return 66.7
    if mcap_yi < 200:
        return 33.3
    return 13.3


def _score_fund(limit_order_amount: float, amount: float, burst: int, max_w: float | None = None) -> float:
    """资金强度 (raw 0-100): 封成比 (封单/成交) + 炸板惩罚
    注: 寻优时 cache_db 无封单字段, 用 amount 排名代理 (见 yaogu_optimizer)
    """
    ratio = limit_order_amount / amount if amount > 0 else 0
    s = 0.0
    if ratio >= 1.5:
        s = 100.0
    elif ratio >= 0.8:
        s = 75.0
    elif ratio >= 0.3:
        s = 45.0
    else:
        s = 20.0
    s -= burst * 10.0  # 每炸一次 -10 (raw 0-100)
    return max(s, 0.0)


def _score_topic(sector: str, sector_zt_count: dict[str, int], max_w: float | None = None) -> float:
    """题材热度 (raw 0-100): 当日该行业涨停数 ≥3 说明有梯队"""
    n = sector_zt_count.get(sector, 0)
    if n >= 5:
        return 100.0
    if n >= 3:
        return 70.0
    if n >= 1:
        return 40.0
    return 10.0


def _score_env(zt_count: int, promo_pct: float | None, max_w: float | None = None) -> float:
    """情绪环境 (raw 0-100): 涨停家数中位 27 / 晋级率中位 21.9% (6b.3)"""
    s = 0.0
    if zt_count >= 80:
        s += 50.0
    elif zt_count >= 50:
        s += 40.0
    elif zt_count >= 30:
        s += 30.0
    elif zt_count >= 15:
        s += 15.0
    if promo_pct is not None:
        if promo_pct >= 30:
            s += 50.0
        elif promo_pct >= 25:
            s += 40.0
        elif promo_pct >= 20:
            s += 30.0
        elif promo_pct >= 15:
            s += 15.0
    return min(s, 100.0)


# R102 情绪周期 4 态 (按调研 6b.3 分位校准: 涨停家中位 27, P25=13, P75=51; 晋级率中位 21.9%)
_ENV_STATE_THRESHOLDS = [
    (80, 30, "极热", "var(--color-danger)"),     # 涨停 ≥80 + 晋级 ≥30% → 极热
    (30, 0,  "高潮", "var(--accent-2)"),          # 涨停 ≥30 → 高潮 (默认开闸)
    (15, 0,  "回暖", "var(--accent)"),            # 涨停 15-30 → 回暖 (关注)
    (0,  0,  "冰点", "var(--ink-3)"),             # 涨停 <15 → 冰点 (空仓)
]


def _env_state(zt_count: int, promo_pct: float | None) -> dict:
    """R102: 情绪周期 4 态状态机.
    Returns { state: '极热/高潮/回暖/冰点', open: bool, color: var(...) }
    """
    if zt_count <= 0:
        return {"state": "冰点", "open": False, "color": "var(--ink-3)",
                "desc": "无涨停数据"}
    if zt_count >= 80 and (promo_pct is None or promo_pct >= 30):
        return {"state": "极热", "open": True, "color": "var(--color-danger)",
                "desc": "高潮期 · 满仓可抓"}
    if zt_count >= 30 and (promo_pct is None or promo_pct >= 25):
        return {"state": "高潮", "open": True, "color": "var(--accent-2)",
                "desc": "赚钱效应 · 闸门开"}
    if zt_count >= 15:
        return {"state": "回暖", "open": False, "color": "var(--accent)",
                "desc": "观察 · 等待晋级率确认"}
    return {"state": "冰点", "open": False, "color": "var(--ink-3)",
            "desc": "空仓观望 · 不抓妖"}


def _stage_label(streak: int, one_word: bool, env_gate: bool) -> str:
    """阶段分类 (调研 §7.3)"""
    if streak >= 6:
        return "加速末期" if env_gate else "加速·闸关"
    if streak >= 3:
        return "主升期"
    return "启动期"


def _one_word(zt: dict) -> bool:
    """一字板: 封板时间 09:30 且 炸板 0 且 换手极低 → 近似 (zt_pool 无开盘价)"""
    ft = str(zt.get("first_time", "") or "").replace(":", "")
    if ft and ft <= "0935":
        return zt.get("burst_count", 0) == 0 and (zt.get("turnover_pct", 100) or 100) < 3
    return False


def _is_lanban(zt: dict) -> bool:
    """烂板识别 (R101 调研支撑: 烂板出妖, 换手充分+有炸板 = 弱转强机会)
    定义: 换手率 ≥20% 且 (炸板 ≥1 或 封单比 < 0.3) → 表示充分分歧
    """
    turn = float(zt.get("turnover_pct", 0) or 0)
    burst = int(zt.get("burst_count", 0) or 0)
    fund_ratio = float(zt.get("limit_order_amount", 0) or 0) / float(zt.get("amount", 0) or 1)
    if turn >= 20 and (burst >= 1 or fund_ratio < 0.3):
        return True
    return False


def _envelope(data=None, error=None, **kw):
    out = {"ok": error is None, "ts": int(systime.time())}
    if error:
        out["error"] = error
    if data is not None:
        out["data"] = data
    out.update(kw)
    return out


def register(app):
    # 启动时一次性读寻优结果 (后续 query 覆盖)
    _w_default, _w_meta = _load_weights()

    def _resolve_weights(qs: dict | None = None) -> dict:
        """query 参数覆盖默认 weights; 未提供则用启动时加载的."""
        if not qs:
            return _w_default
        out = dict(_w_default)
        for d in _WEIGHT_DIMS:
            v = qs.get(f"w_{d}")
            if v is not None:
                try:
                    out[d] = float(v)
                except (TypeError, ValueError):
                    pass
        return out

    # ── GET /api/yaogu/live — 今日妖股榜单 ──
    @app.get("/api/yaogu/live")
    async def api_yaogu_live(
        w_streak: float | None = None, w_turn: float | None = None,
        w_mcap: float | None = None, w_fund: float | None = None,
        w_topic: float | None = None, w_env: float | None = None,
        mode: str = "trading",  # trading = 盘前/盘中 (受闸门限制); review = 盘后查看 (不受闸门限制)
        fresh: int = 0,  # 1 = 跳过缓存 (force refresh)
    ):
        # 2026-08-12 R-perf2: 加权重 + mode + 日期的 60s 缓存, 同 worker 二次访问秒返.
        # 原来每个请求都全量 fetch_zt_pool → 10s+, 加缓存 → 10ms
        from .. import cache_store as _cs
        from .server import _store_get, _store_set
        cache_key = _cs.K.YAOGU_LIVE.format(
            w_streak=w_streak or 0, w_turn=w_turn or 0, w_mcap=w_mcap or 0,
            w_fund=w_fund or 0, w_topic=w_topic or 0, w_env=w_env or 0,
            mode=mode)
        if not fresh:
            cached = _store_get(cache_key, ttl=60)
            if cached:
                cached["_cache_hit"] = True
                return _envelope(cached)
        result = await _build_yaogu_live(w_streak, w_turn, w_mcap, w_fund, w_topic, w_env, mode)
        # 写 60s 缓存 (跨 worker 共享)
        try:
            _store_set(cache_key, result, ttl=60)
        except Exception:
            pass
        return _envelope(result)

    async def _build_yaogu_live(w_streak, w_turn, w_mcap, w_fund, w_topic, w_env, mode):
        t0 = systime.time()
        today = datetime.now().strftime("%Y%m%d")
        weights = _resolve_weights({"w_streak": w_streak, "w_turn": w_turn,
                                    "w_mcap": w_mcap, "w_fund": w_fund,
                                    "w_topic": w_topic, "w_env": w_env})
        try:
            zt_pool = msf.fetch_zt_pool(today)
        except Exception as e:
            log.warning("fetch_zt_pool %s 失败: %s", today, e)
            zt_pool = []
        if not zt_pool:
            # 非交易日/数据缺失: 取最近 3 日
            recent = msf.fetch_recent_zt_pool(days=3)
            for d in sorted(recent.keys(), reverse=True):
                zt_pool = list(recent[d].values())
                today = d
                if zt_pool:
                    break

        # 昨日涨停池 → 断板检测 + 晋级率 (fetch_recent_zt_pool 是跨日聚合, 需单独拉昨日)
        prev_pool: dict = {}
        promo_pct: float | None = None
        try:
            td_all = msf.fetch_trade_dates() or []
            prev_dates = [d.replace("-", "") for d in sorted(td_all) if d.replace("-", "") < today]
            if prev_dates:
                for pz in msf.fetch_zt_pool(prev_dates[-1]) or []:
                    prev_pool[pz["code"]] = pz
                prev_codes = set(prev_pool)
                cur_codes = {z["code"] for z in zt_pool}
                if prev_codes:
                    overlap = len(prev_codes & cur_codes) / len(prev_codes)
                    if overlap > 0.9:
                        # R2000.3 (2026-08-16): 非交易日时 fetch_zt_pool(today) 返回上一交易日数据,
                        # 100% 重合是预期现象 → 改用 prev[-1] vs prev[-2] 算 "昨日晋级率"
                        if len(prev_dates) >= 2:
                            try:
                                earlier_pool = msf.fetch_zt_pool(prev_dates[-2]) or []
                                earlier_codes = {z["code"] for z in earlier_pool}
                                if earlier_codes:
                                    e_overlap = len(prev_codes & earlier_codes) / len(prev_codes)
                                    promo_pct = round(e_overlap * 100, 1)
                            except Exception as _e:
                                log.warning("prev_2 zt pool 失败: %s", _e)
                        # 单层 fallback: 兜底 0
                        if promo_pct is None:
                            promo_pct = 0.0
                    else:
                        promo_pct = round(overlap * 100, 1)
        except Exception as e:
            log.warning("prev zt pool 失败: %s", e)

        zt_count = len(zt_pool)
        env_st = _env_state(zt_count, promo_pct)  # R102 4 态
        env_gate = env_st["open"]

        # 行业涨停数 (题材热度)
        sector_zt_count: dict[str, int] = {}
        for z in zt_pool:
            sec = str(z.get("sector", "") or "")
            if sec:
                sector_zt_count[sec] = sector_zt_count.get(sec, 0) + 1

        # 妖性评分 (raw 0-100 × W / 100 = 加权后贡献)
        stocks = []
        signals = []
        for z in sorted(zt_pool, key=lambda x: x.get("streak", 1), reverse=True):
            streak = int(z.get("streak", 1) or 1)
            if streak < 2:
                continue
            mcap = float(z.get("market_cap", 0) or 0) / 1e8  # 亿
            turn = float(z.get("turnover_pct", 0) or 0)
            ow = _one_word(z)
            r_streak = _score_streak(streak)
            r_turn = _score_turn(turn)
            r_mcap = _score_mcap(mcap)
            r_fund = _score_fund(float(z.get("limit_order_amount", 0) or 0),
                                 float(z.get("amount", 0) or 0),
                                 int(z.get("burst_count", 0) or 0))
            r_topic = _score_topic(str(z.get("sector", "") or ""), sector_zt_count)
            r_env = _score_env(zt_count, promo_pct)
            s_streak = round(r_streak * weights["streak"] / 100, 1)
            s_turn = round(r_turn * weights["turn"] / 100, 1)
            s_mcap = round(r_mcap * weights["mcap"] / 100, 1)
            s_fund = round(r_fund * weights["fund"] / 100, 1)
            s_topic = round(r_topic * weights["topic"] / 100, 1)
            s_env = round(r_env * weights["env"] / 100, 1)
            score = round(s_streak + s_turn + s_mcap + s_fund + s_topic + s_env, 1)
            stage = _stage_label(streak, ow, env_gate)
            lb = _is_lanban(z)
            stocks.append({
                "code": z["code"], "name": z["name"], "streak": streak,
                "score": score, "stage": stage, "one_word": ow, "lanban": lb,
                "mcap_yi": round(mcap, 1) if mcap > 0 else None,
                "turnover": round(turn, 1) if turn > 0 else None,
                "burst": int(z.get("burst_count", 0) or 0),
                "sealed": z.get("first_time", ""),
                "fund_ratio": round(float(z.get("limit_order_amount", 0) or 0) / float(z.get("amount", 0) or 0), 2)
                if float(z.get("amount", 0) or 0) > 0 else None,
                "sector": z.get("sector", ""),
                "factors": {"streak": s_streak, "turn": s_turn, "mcap": s_mcap,
                            "fund": s_fund, "topic": s_topic, "env": s_env},
            })
        stocks.sort(key=lambda s: s["score"], reverse=True)

        # 抓取信号 (2/3 板, 非一字, 闸门开) — R101: 烂板加权
        if env_gate:
            for s in stocks:
                if s["one_word"]:
                    continue
                if s["streak"] in (2, 3):
                    # 烂板加权: 烂板出妖是市场共识, 调研支撑
                    final_score = s["score"] + 8 if s.get("lanban") else s["score"]
                    if s["streak"] == 2:
                        sig_type = "2板烂板⭐" if s.get("lanban") else "2板确认"
                    else:
                        sig_type = "3板烂板⭐" if s.get("lanban") else "3板主升"
                    signals.append({
                        "code": s["code"], "name": s["name"], "streak": s["streak"],
                        "type": sig_type, "score": s["score"], "final_score": final_score,
                        "lanban": bool(s.get("lanban")),
                    })
            signals.sort(key=lambda s: s.get("final_score", s["score"]), reverse=True)

        # 断板预警 (昨日涨停 ≥2 板, 今日未涨停)
        watch = []
        for code, prev in prev_pool.items():
            p = prev if isinstance(prev, dict) else {}
            pstreak = int(p.get("streak", 1) or 1)
            if pstreak >= 2 and code not in {z["code"] for z in zt_pool}:
                watch.append({"code": code, "name": p.get("name", code),
                              "streak": pstreak, "note": f"{pstreak}板断板"})
        watch.sort(key=lambda w: w["streak"], reverse=True)

        # 盘后模式 (review): 闸门关时仍展示信号,标记为"次日观察池"
        review_mode = mode == "review"
        signals_out = signals
        if review_mode and not env_gate:
            # 闸门关 + 盘后: 降级显示所有 2/3 板 (即使一字), 标记为"仅供次日参考"
            review_signals = []
            for s in stocks:
                if s["streak"] in (2, 3):
                    final_score = s["score"] + 8 if s.get("lanban") else s["score"]
                    if s["streak"] == 2:
                        sig_type = "2板烂板⭐" if s.get("lanban") else "2板确认"
                    else:
                        sig_type = "3板烂板⭐" if s.get("lanban") else "3板主升"
                    review_signals.append({
                        "code": s["code"], "name": s["name"], "streak": s["streak"],
                        "type": sig_type, "score": s["score"], "final_score": final_score,
                        "lanban": bool(s.get("lanban")),
                        "one_word": bool(s.get("one_word", False)),
                    })
            review_signals.sort(key=lambda s: s.get("final_score", s["score"]), reverse=True)
            signals_out = review_signals

        return {
            "date": today,
            "env": {"zt_count": zt_count, "promo_pct": promo_pct,
                    "gate": env_gate, "state": env_st["state"], "color": env_st["color"],
                    "desc": env_st["desc"],
                    "gate_params": [ybt.ZT_COUNT_MIN, ybt.PROMO_MIN]},
            "stocks": stocks[:30],
            "signals": signals_out,
            "watch": watch[:20],
            "weights": weights,
            "weights_meta": _w_meta,
            "review_mode": review_mode,
            "signals_suppressed": (not env_gate and not review_mode and len(signals) == 0),
            "elapsed_s": round(systime.time() - t0, 2),
        }

    # ── GET /api/yaogu/backtest — 妖股回测 ──
    @app.get("/api/yaogu/backtest")
    async def api_yaogu_backtest(
        start: str = ybt.BT_START, end: str = ybt.BT_END,
        entry: int = ybt.ENTRY_STREAK,
        exit_rule: str = ybt.EXIT_RULE,
        zt_min: int = ybt.ZT_COUNT_MIN,
        promo_min: float = ybt.PROMO_MIN,
        gate: int = 1,
        stop_loss: float = -8.0,
    ):
        try:
            r = ybt.run_yaogu_backtest(
                start=start, end=end, entry_streak=entry, exit_rule=exit_rule,
                zt_count_min=zt_min, promo_min=promo_min, gate_enabled=bool(gate),
                stop_loss_pct=stop_loss)
            return _envelope(r)
        except Exception as e:
            log.exception("yaogu backtest 失败")
            return _envelope(error=f"回测失败: {e}")

    # ── GET /api/yaogu/backtest_lanban — R101 烂板子集回测 ──
    @app.get("/api/yaogu/backtest_lanban")
    async def api_yaogu_backtest_lanban(
        start: str = ybt.BT_START, end: str = ybt.BT_END,
        entry: int = ybt.ENTRY_STREAK,
        exit_rule: str = ybt.EXIT_RULE,
        zt_min: int = 1,
        promo_min: float = 0,
        gate: int = 0,
        stop_loss: float = -8.0,
    ):
        try:
            r = ybt.run_lanban_backtest(
                start=start, end=end, entry_streak=entry, exit_rule=exit_rule,
                zt_count_min=zt_min, promo_min=promo_min, gate_enabled=bool(gate),
                stop_loss_pct=stop_loss)
            return _envelope(r)
        except Exception as e:
            log.exception("yaogu lanban backtest 失败")
            return _envelope(error=f"烂板回测失败: {e}")

    # ── GET /api/yaogu/params — 评分参数 ──
    @app.get("/api/yaogu/params")
    async def api_yaogu_params():
        return _envelope({
            "weights": _w_default,
            "weights_meta": _w_meta,
            "gate": {"zt_count_min": ybt.ZT_COUNT_MIN, "promo_min": ybt.PROMO_MIN},
            "bt": {"entry_streak": ybt.ENTRY_STREAK, "exit_rule": ybt.EXIT_RULE,
                   "start": ybt.BT_START, "end": ybt.BT_END},
            "survey": "YAOGU_500_SURVEY.md §7.2",
        })

    # ── GET /api/yaogu/gbm_report — R116 GBR 150 维训练报告 (前端展示卡) ──
    @app.get("/api/yaogu/gbm_report")
    async def api_yaogu_gbm_report():
        report_path = Path(__file__).parent.parent / "yaogu_gbm_v2_report.json"
        try:
            with open(report_path, "r") as f:
                report = json.load(f)
            return _envelope(report)
        except Exception as e:
            return _envelope(error=f"读取 R116 报告失败: {e}")
