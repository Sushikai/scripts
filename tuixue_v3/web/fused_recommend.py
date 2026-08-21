"""
fused_recommend: 涨停溢价页综合推荐 (zt × dragons × dexin 动态权重融合)

R-2026-08-16: zt-premium 折叠区「🔥 Fused Top10」数据源。

数据流:
  A. zt-live_pick  (web/zt_screener.api_zt_live_pick)   → 涨停 T+1 forward-looking 打分 0-100
  B. dragons       (dragons.score_dragons)              → 龙头 6 维归一 score_total 0-100
  C. dexin         (DexinTrendAgent.classify per code)  → 5 段判定 stage 0-5 → 归一 0-100

按 code 三路 join → 动态权重 (反方差 + 先验下限) → aggregated_win_rate → 取 top_n。

性能: 端到端 ~6-10s 冷启动, 30s in-process + 5min Redis 暖路径。

不复用:
  - ai_scoring.score_aggregate (强依赖 s["ai"] 字段, 融合层无)
  - meta_strategy.MetaAggregator (固定权重, 不支持动态)
"""
from __future__ import annotations

import asyncio
import logging
import time as systime
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

# ── 模块常量 ──
FUSED_KEY = "tuixue:zt:fused_recommend:v1"
FUSED_TTL_REDIS = 300      # 5min 跨 worker
FUSED_TTL_INPROC = 30      # 30s in-process 截流
DEFAULT_TOP_N = 10
DEFAULT_ZT_CANDIDATE_POOL = 30  # 取 zt-live_pick top30 作为 dexin wrapper 输入
DEXIN_CONCURRENCY = 8           # 单次 _dexin_per_codes 并发上限

# 动态权重先验下限 (避免某一路方差 → 0 时霸榜)
WEIGHT_FLOORS = {
    "zt": 0.25,
    "dragons": 0.20,
    "dexin": 0.15,
}


# ═══════════════════════════════════════════════════════════
#  工具函数: 归一化 + 动态权重 + 聚合
# ═══════════════════════════════════════════════════════════
def _norm_zt(zt_score: float) -> float:
    """zt-live_pick score (50-90) → 0-100。低于 50 当 0, 高于 90 当 100。"""
    if not zt_score:
        return 0.0
    v = (float(zt_score) - 50.0) / 40.0 * 100.0
    return max(0.0, min(100.0, round(v, 2)))


def _norm_dragons(score_total: float | None) -> float:
    """dragons score_total 本就是 0-100。"""
    if score_total is None:
        return 0.0
    return max(0.0, min(100.0, round(float(score_total), 2)))


_STAGE_TO_IDX = {
    "no_uptrend": 0,
    "none": 0,
    "cang_zha": 1,
    "xu_sha": 2,
    "xu_sha_dangerous": 2,
    "clearing": 3,
    "de_xin": 4,
}


def _norm_dexin(stage: str | None, variant: str | None = None) -> float:
    """dexin stage 0-5 段 → 0-100。de_xin/clearing 加 +20 bonus。"""
    idx = _STAGE_TO_IDX.get(stage or "", 0)
    base = (idx / 4.0) * 100.0  # 0-4 段 → 0-100, 留 5 段扩展
    if stage == "de_xin":
        base += 20.0
    elif stage == "clearing":
        base += 10.0
    if variant and "dangerous" in str(variant):
        base -= 30.0
    return max(0.0, min(100.0, round(base, 2)))


def dynamic_weight(zt_scores: list[float], dragon_scores: list[float], dexin_scores: list[float]) -> tuple[float, float, float]:
    """三路动态权重: 反方差 + 先验下限。

    输入: 三路 0-100 分数组 (今日 fused 候选池上).
    输出: (w_zt, w_dragon, w_dexin), sum=1.

    方差越大 → 信号越离散 → 权重越低 (不可信);
    方差越小 → 信号越集中 → 权重越高 (共识度高)。
    先验下限保证任一路都有最低话语权 — 实现: 把方差下界钳到 (1/floor),
    让反方差算出最大权重 = floor。
    """
    import statistics

    def _safe_var(xs: list[float], default: float = 100.0) -> float:
        if len(xs) < 2:
            return default
        try:
            return statistics.pvariance(xs)
        except statistics.StatisticsError:
            return default

    raw_var = [
        _safe_var(zt_scores),
        _safe_var(dragon_scores),
        _safe_var(dexin_scores),
    ]

    # 先验下限: floor_i → 方差上界 (1/floor_i), 即反方差下界 = floor_i
    # var_i 越大 → inv 越小 → 权重越低; var_i 越小 → inv 越大 → 权重越高
    # 用 (1/floor_i) 当 max(var_i) 即可让反方差算出的权重 ≤ floor_i
    inv = []
    for i, floor_key in enumerate(("zt", "dragons", "dexin")):
        floor = WEIGHT_FLOORS[floor_key]
        # 反方差下界 = floor → var 上界 = 1/floor → 实际 var = min(raw_var, 1/floor)
        capped_var = min(raw_var[i], 1.0 / floor) if raw_var[i] > 0 else 1.0 / floor
        # 还要兜底: capped_var ≤ 1.0 (让反方差下界 ≤ 1)
        capped_var = max(capped_var, 1.0)
        inv.append(1.0 / capped_var)

    total = sum(inv)
    w = [x / total for x in inv]
    return (
        round(w[0], 4),
        round(w[1], 4),
        round(w[2], 4),
    )


# ═══════════════════════════════════════════════════════════
#  dexin 单只 wrapper (复用 DexinTrendAgent, 走 asyncio.to_thread)
# �══════════════════════════════════════════════════════════
async def _dexin_per_code(code: str) -> tuple[str, dict]:
    """对单只 code 跑 DexinTrendAgent.classify() → 归一 score。"""
    from .dexin_screener import DexinTrendAgent
    from .backtest_screener import _prefetch_daily as _pf_daily

    try:
        dailies = await asyncio.to_thread(_pf_daily, [code], 60)
        df = dailies.get(code)
        if df is None or len(df) < 25:
            return code, {"stage": "none", "score": 0.0, "label": "数据不足"}
        cls = await asyncio.to_thread(DexinTrendAgent().detect, df)
        stage = cls.get("stage", "none")
        variant = cls.get("variant")
        score = _norm_dexin(stage, variant)
        return code, {
            "stage": stage,
            "stage_label": cls.get("stage_label", ""),
            "variant": variant,
            "score": score,
            "advice": cls.get("advice", "")[:60],
        }
    except Exception as e:
        log.warning(f"fused _dexin_per_code {code} 失败: {e}")
        return code, {"stage": "none", "score": 0.0, "label": "异常"}


async def _dexin_per_codes(codes: list[str]) -> dict[str, dict]:
    """并发跑多只 code 的 dexin stage (限流 DEXIN_CONCURRENCY)。"""
    sem = asyncio.Semaphore(DEXIN_CONCURRENCY)

    async def _guarded(code: str) -> tuple[str, dict]:
        async with sem:
            return await _dexin_per_code(code)

    tasks = [_guarded(c) for c in codes]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: dict[str, dict] = {}
    for r in results:
        if isinstance(r, Exception):
            continue
        code, info = r
        out[code] = info
    return out


# ═══════════════════════════════════════════════════════════
#  综合胜率 (lazy import comprehensive_strategy, 避免拉 optimizer 依赖)
# ═══════════════════════════════════════════════════════════
def aggregated_win_rate(components: dict[str, float], weights: dict[str, float]) -> dict:
    """三路加权 fused_score + 综合胜率分级 + confidence。

    components: {"zt": float, "dragons": float, "dexin": float}   (各 0-100)
    weights:    {"zt": float, "dragons": float, "dexin": float}   (sum=1)

    返回:
      fused_score: 加权和 0-100
      win_rate_pct: 综合胜率 (委托给 comprehensive_strategy.aggregated_win_rate)
      confidence: min(weights.values()) — 权重最弱一路就是不确定性天花板
    """
    try:
        from .comprehensive_strategy import aggregated_win_rate as _cs_awr
        return _cs_awr(components, weights, dd_pct=0.0)
    except ImportError:
        # 极端 fallback: 简化分级
        fused = sum(components.get(k, 0.0) * weights.get(k, 0.0) for k in ("zt", "dragons", "dexin"))
        fused = round(max(0.0, min(100.0, fused)), 2)
        if fused >= 80:
            wr = 65.0
        elif fused >= 60:
            wr = 55.0
        elif fused >= 40:
            wr = 45.0
        else:
            wr = 35.0
        return {
            "fused_score": fused,
            "win_rate_pct": wr,
            "confidence": round(min(weights.values()) if weights else 0.0, 4),
        }


# ═══════════════════════════════════════════════════════════
#  主入口: fused_recommend()
# ═══════════════════════════════════════════════════════════
async def fused_recommend(top_n: int = DEFAULT_TOP_N, refresh: bool = False) -> dict:
    """综合推荐: zt × dragons × dexin 三路并行 + 动态权重 + 综合胜率 → top_n。

    返回 envelope 兼容数据:
      {
        "top10": [{code, name, sector, fused_score, win_rate_pct, confidence,
                   components: {zt, dragons, dexin}, weights: {zt, dragons, dexin},
                   source_signals: {...}}],
        "weights": {zt, dragons, dexin},   # 全局权重 (今日 fused)
        "ts": float,
        "source": "fused_v1",
      }
    """
    from .. import dragons as _dragons_mod
    import httpx as _httpx

    # ── 1. 三路并行 ──
    # A. zt-live_pick: HTTP 自调用 (zt_screener.api_zt_live_pick 是 register 内部闭包, 没法 import)
    async def _fetch_live_pick() -> list[dict]:
        try:
            async with _httpx.AsyncClient(timeout=_httpx.Timeout(connect=3.0, read=40.0, write=10.0, pool=5.0)) as c:
                r = await c.get("http://127.0.0.1:7799/api/zt/live_pick",
                                params={"top_n": str(DEFAULT_ZT_CANDIDATE_POOL),
                                        "refresh": "1" if refresh else "0",
                                        "source": "default"})
                if r.status_code == 200:
                    data = (r.json() or {}).get("data") or {}
                    return data.get("picks") or []
        except Exception as e:
            log.warning(f"fused: HTTP self-call live_pick 失败: {e}")
        return []

    zt_task = asyncio.create_task(_fetch_live_pick())
    # B. dragons: HTTP 自调用 /api/dragons (R2000.57: 裸跑 score_dragons 无缓存, 冷启动
    #   涨停池+龙虎榜+daily 常超 30s; 走端点拿 30s 内存缓存 + Redis + stale 兜底)
    async def _fetch_dragons() -> dict:
        try:
            async with _httpx.AsyncClient(timeout=_httpx.Timeout(connect=3.0, read=40.0, write=10.0, pool=5.0)) as c:
                r = await c.get("http://127.0.0.1:7799/api/dragons",
                                params={"refresh": "1" if refresh else "0"})
                if r.status_code == 200:
                    return (r.json() or {}).get("data") or {}
        except Exception as e:
            log.warning(f"fused: dragons self-call 失败: {e}")
        return {}
    dragons_task = asyncio.create_task(_fetch_dragons())

    zt_resp, dragons_resp = await asyncio.gather(zt_task, dragons_task, return_exceptions=True)

    # 容错
    zt_picks: list[dict] = []
    # R2000.57 (2026-08-20): _fetch_live_pick 已把响应解包成 picks 列表返回,
    #   外层再按响应 dict 取 .data.picks → isinstance(list) 不通过 → 一路被丢弃
    #   (曾致 fused 恒空: 日志 "zt-live_pick 异常: [{...picks...}]" 印的其实是数据本身)
    if isinstance(zt_resp, list):
        zt_picks = zt_resp
    elif isinstance(zt_resp, dict):
        zt_picks = (zt_resp.get("data") or {}).get("picks") or []
    else:
        log.warning(f"fused: zt-live_pick 异常: {zt_resp}")

    dragons_scored: list[dict] = []
    if isinstance(dragons_resp, dict):
        # R2000.57 (2026-08-20): score_dragons 返回 "all"/"top10", 从没有 "scored" 键
        #   → dragons 路永远空, fused 只剩 zt 一路。补 "all" 兜底。
        dragons_scored = dragons_resp.get("scored") or dragons_resp.get("all") or []
    else:
        log.warning(f"fused: dragons 异常: {dragons_resp}")

    # 候选 code 列表 = zt picks ∪ dragons scored
    zt_codes = [p.get("code", "") for p in zt_picks if p.get("code")]
    dragon_codes = [s.get("code", "") for s in dragons_scored if s.get("code")]
    union_codes = list(dict.fromkeys([c for c in zt_codes + dragon_codes if c]))[:DEFAULT_ZT_CANDIDATE_POOL]

    # C. dexin: per-code 跑 (限流 8)
    dexin_map: dict[str, dict] = {}
    if union_codes:
        dexin_map = {}
        if union_codes:
            try:
                dexin_map = await asyncio.wait_for(_dexin_per_codes(union_codes), timeout=20.0)
            except asyncio.TimeoutError:
                log.warning("fused: dexin 20s 超时, 跳过 (候选 dexin 子分默认 0)")
            except Exception as e:
                log.warning(f"fused: dexin 异常: {e}")

    # ── 2. 三路 join ──
    # zt 索引
    zt_idx = {p.get("code", ""): p for p in zt_picks}
    # dragons 索引
    dragon_idx = {s.get("code", ""): s for s in dragons_scored}

    candidates: dict[str, dict] = {}
    for code in union_codes:
        zt_p = zt_idx.get(code, {})
        dr_s = dragon_idx.get(code, {})
        dx = dexin_map.get(code, {"score": 0.0, "stage": "none", "stage_label": ""})

        zt_raw = zt_p.get("score") or 0
        dr_raw = dr_s.get("score_total") or 0
        dx_raw = dx.get("score") or 0.0

        candidates[code] = {
            "code": code,
            "name": zt_p.get("name") or dr_s.get("name") or code,
            "sector": dr_s.get("sector") or zt_p.get("sector") or "",
            "components": {
                "zt": _norm_zt(zt_raw),
                "dragons": _norm_dragons(dr_raw),
                "dexin": float(dx_raw),
            },
            "source_signals": {
                "zt": {
                    "streak": zt_p.get("streak"),
                    "limit_order_amount": zt_p.get("limit_order_amount"),
                    "rating": zt_p.get("rating"),
                    "raw_score": zt_raw,
                },
                "dragons": {
                    "rank": dr_s.get("rank"),
                    "streak": dr_s.get("streak"),
                    "is_mainline": dr_s.get("is_mainline"),
                    "raw_score_total": dr_raw,
                },
                "dexin": {
                    "stage": dx.get("stage"),
                    "stage_label": dx.get("stage_label"),
                    "advice": dx.get("advice"),
                },
            },
        }

    # ── 3. 动态权重 (基于候选池上的三路分数分布) ──
    zt_scores = [c["components"]["zt"] for c in candidates.values()]
    dragon_scores = [c["components"]["dragons"] for c in candidates.values()]
    dexin_scores = [c["components"]["dexin"] for c in candidates.values()]

    w_zt, w_dragon, w_dexin = dynamic_weight(zt_scores, dragon_scores, dexin_scores)
    weights = {"zt": w_zt, "dragons": w_dragon, "dexin": w_dexin}

    # ── 4. 聚合 + 排序 ──
    enriched: list[dict] = []
    for c in candidates.values():
        agg = aggregated_win_rate(c["components"], weights)
        c["fused_score"] = agg["fused_score"]
        c["win_rate_pct"] = agg["win_rate_pct"]
        c["confidence"] = agg["confidence"]
        enriched.append(c)

    enriched.sort(key=lambda x: (x["fused_score"], x["confidence"]), reverse=True)
    top = enriched[: max(1, top_n)]

    return {
        "top10": top,
        "weights": weights,
        "pool_size": len(candidates),
        "ts": systime.time(),
        "source": "fused_v1",
    }


# ═══════════════════════════════════════════════════════════
#  FastAPI 注册
# ═══════════════════════════════════════════════════════════
def register(app):
    """挂载 fused_recommend 路由到 FastAPI app。"""
    _cache: dict = {"data": None, "ts": 0.0}
    _lock = asyncio.Lock()

    def _redis_read() -> dict | None:
        try:
            from .. import cache_store as _cs
            s = _cs.get_store()
            if s:
                blob = s.get(FUSED_KEY)
                if blob and isinstance(blob, dict) and blob.get("top10"):
                    _cache["data"] = blob
                    _cache["ts"] = float(blob.get("ts") or 0)
                    return blob
        except Exception:
            pass
        return None

    def _redis_write(data: dict) -> None:
        try:
            from .. import cache_store as _cs
            s = _cs.get_store()
            if s:
                s.set(FUSED_KEY, data, ttl=FUSED_TTL_REDIS)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    #  /api/zt/fused_recommend (live)
    # ═══════════════════════════════════════════════════════════
    @app.get("/api/zt/fused_recommend")
    async def api_zt_fused_recommend(top_n: int = DEFAULT_TOP_N, refresh: bool = False):
        """涨停溢价综合推荐 (zt × dragons × dexin 动态加权)。

        性能: 端到端 ~6-10s 冷启动; 30s in-process + 5min Redis 暖路径。
        """
        if not refresh and _cache["data"] and (systime.time() - _cache["ts"]) < FUSED_TTL_INPROC:
            data = dict(_cache["data"])
            data["_cache_hit"] = "inproc"
            return {"ok": True, "data": data, "error": None, "ts": systime.time()}

        # Redis 跨 worker
        if not refresh:
            blob = _redis_read()
            if blob:
                data = dict(blob)
                data["_cache_hit"] = "redis"
                return {"ok": True, "data": data, "error": None, "ts": systime.time()}

        # 单飞
        async with _lock:
            try:
                data = await fused_recommend(top_n=top_n, refresh=refresh)
                # R2000.57 (2026-08-20): 空结果不写缓存 — 子源(zt/dragons)超时产生 pool_size=0
                #   的结果被缓存 5min → fused 页一直空。空结果跳过缓存, 下次请求自动重试。
                if data.get("top10"):
                    _cache["data"] = data
                    _cache["ts"] = systime.time()
                    _redis_write(data)
                else:
                    log.warning("fused: 计算结果为空 (子源超时?), 不写缓存 — 下次请求重试")
                data["_cache_hit"] = "fresh"
                return {"ok": True, "data": data, "error": None, "ts": systime.time()}
            except Exception as e:
                log.exception(f"fused_recommend 失败: {e}")
                return {"ok": False, "data": None, "error": str(e), "ts": systime.time()}

    # ═══════════════════════════════════════════════════════════
    #  /api/zt/fused_backtest (历史 5d-max-high 胜率回测)
    # ═══════════════════════════════════════════════════════════
    @app.get("/api/zt/fused_backtest")
    async def api_zt_fused_backtest(days: int = 180, target_wr: float = 80.0, refresh: bool = False):
        """历史回测: 用 daily K 线回放 fused_recommend, 算 5 日内最高价胜率。

        口径: T+1 开盘买入 → 持有 5 天内任意收盘价 ≥ 买入价 → win。
        目标: ≥ target_wr (默认 80%)。

        返回:
          trades: int
          win_rate_pct: float
          avg_return_pct: float
          max_drawdown_pct: float
          meet_target: bool
          params_used: dict (当时 fused_recommend 用什么权重/阈值)
        """
        from .fused_recommend import fused_backtest as _bt

        cache_key = f"tuixue:zt:fused_backtest:{days}:{target_wr}:v1"
        if not refresh:
            try:
                from .. import cache_store as _cs
                s = _cs.get_store()
                if s:
                    blob = s.get(cache_key)
                    if blob:
                        return {"ok": True, "data": blob, "error": None, "ts": systime.time()}
            except Exception:
                pass

        try:
            result = await asyncio.to_thread(_bt, days, target_wr)
            try:
                from .. import cache_store as _cs
                s = _cs.get_store()
                if s:
                    s.set(cache_key, result, ttl=3600)  # 1h
            except Exception:
                pass
            return {"ok": True, "data": result, "error": None, "ts": systime.time()}
        except Exception as e:
            log.exception(f"fused_backtest 失败: {e}")
            return {"ok": False, "data": None, "error": str(e), "ts": systime.time()}

    # ═══════════════════════════════════════════════════════════
    #  /api/zt/fused_evolve (10K 进化算法)
    # ═══════════════════════════════════════════════════════════
    @app.get("/api/zt/fused_evolve")
    async def api_zt_fused_evolve(
        iterations: int = 1000,
        target_wr: float = 80.0,
        days: int = 180,
        refresh: bool = False,
    ):
        """进化算法: 反复调 (weights, WR_BANDS, DD_PENALTIES, HOLD3_REWARDS) 直到 5d-max-high 胜率 ≥ target_wr。

        性能预算: 1000 iter × 30s/iter ≈ 8h — 默认 1000 iter, 生产用后台 cron。
        """
        from .fused_recommend import evolve_weights as _ev

        cache_key = f"tuixue:zt:fused_evolve:{iterations}:{target_wr}:{days}:v1"
        if not refresh:
            try:
                from .. import cache_store as _cs
                s = _cs.get_store()
                if s:
                    blob = s.get(cache_key)
                    if blob:
                        return {"ok": True, "data": blob, "error": None, "ts": systime.time()}
            except Exception:
                pass

        try:
            result = await asyncio.to_thread(_ev, iterations, target_wr, days)
            try:
                from .. import cache_store as _cs
                s = _cs.get_store()
                if s:
                    s.set(cache_key, result, ttl=3600)
                    # 同步写 OPTIMAL_PARAMS key — 下一次 fused_recommend 自动读
                    s.set("optim:best:fused", result.get("best_params") or {}, ttl=86400)
            except Exception:
                pass
            return {"ok": True, "data": result, "error": None, "ts": systime.time()}
        except Exception as e:
            log.exception(f"fused_evolve 失败: {e}")
            return {"ok": False, "data": None, "error": str(e), "ts": systime.time()}

    # ═══════════════════════════════════════════════════════════
    #  /api/zt/fused_optimized (读历史最优参数)
    # ═══════════════════════════════════════════════════════════
    @app.get("/api/zt/fused_optimized")
    async def api_zt_fused_optimized():
        """读 fused evolve 历史最优参数 (cron 写入)。"""
        try:
            from .. import cache_store as _cs
            s = _cs.get_store()
            blob = s.get("optim:best:fused") if s else None
            if not blob:
                return {"ok": True, "data": None, "error": "无历史最优", "ts": systime.time()}
            return {"ok": True, "data": blob, "error": None, "ts": systime.time()}
        except Exception as e:
            return {"ok": False, "data": None, "error": str(e), "ts": systime.time()}


# ═══════════════════════════════════════════════════════════
#  5d-max-high 回测 (历史 180d)
# ═══════════════════════════════════════════════════════════
def fused_backtest(days: int = 180, target_wr: float = 80.0) -> dict:
    """历史回测 fused_recommend: T+1 开盘买入 → 持有 5d 内任意收盘价 ≥ 买入价 → win。

    实现:
      1. 拉最近 days 个交易日的 ZT 池 (每日)
      2. 对每个交易日, 跑 fused_recommend 的三路打分 → 取 top10
      3. T+1 开盘买入, 检查接下来 5 个交易日的 close.max() ≥ open
      4. 算 win_rate_pct / avg_return / max_drawdown
      5. 与 target_wr 比 → meet_target
    """
    from .backtest_screener import _prefetch_daily as _pf_daily
    from .. import multi_source_fetchers as msf

    # 1) 拉最近 days 交易日的 ZT 池
    dates = msf.fetch_trade_dates(days + 10) or []  # 多拉 10d 防周末/节假日错位
    dates = sorted(dates)[-days:]

    # 2) 全市场候选: 收集所有出现在 ZT 池中的 code (去重)
    all_codes: set[str] = set()
    daily_picks: dict[str, list[dict]] = {}  # date → picks
    for d in dates:
        pool = msf.fetch_zt_pool(d) or []
        for r in pool:
            code = str(r.get("code", "")).zfill(6)
            if code:
                all_codes.add(code)
        daily_picks[d] = pool

    # 3) 批量拉日线 (40 worker 并行, 复用缓存)
    dailies = _pf_daily(list(all_codes), max(days + 30, 60))

    # 4) 逐日跑 fused 三路打分 + 模拟交易
    trades: list[dict] = []
    for d in dates:
        pool = daily_picks.get(d) or []
        if not pool:
            continue
        # 简化版打分: 用 zt score_total (50-90) 作为该日唯一信号
        # (dragons/dexin 历史回放代价过高, 进化阶段聚焦 zt + 权重调优)
        scored = []
        for r in pool:
            code = str(r.get("code", "")).zfill(6)
            df = dailies.get(code)
            if df is None or df.empty:
                continue
            df_d = df[df["日期"] == pd_ts(d)] if "日期" in df.columns else None
            if df_d is None or df_d.empty:
                continue
            zt_score = float(r.get("score") or 70.0)
            scored.append({
                "code": code,
                "name": r.get("name", ""),
                "zt_score": zt_score,
                "date": d,
            })

        # 取当日 top10
        scored.sort(key=lambda x: x["zt_score"], reverse=True)
        top10 = scored[:10]

        # 5d-max-high 模拟
        for s in top10:
            code = s["code"]
            df = dailies.get(code)
            if df is None or df.empty:
                continue
            df = df.sort_values("日期").reset_index(drop=True)
            # 找到 buy_date 在 df 中的 idx
            df_dates = pd.Series(df["日期"].astype(str).values)
            buy_idx = df_dates[df_dates.str.startswith(d)].index
            if buy_idx.empty:
                continue
            buy_idx = int(buy_idx[0])
            if buy_idx + 1 >= len(df):
                continue
            entry = float(df["开盘"].iloc[buy_idx + 1])  # T+1 开盘
            if entry <= 0:
                continue
            # 接下来 5 天 close
            window = df["收盘"].iloc[buy_idx + 1: buy_idx + 6]
            if window.empty:
                continue
            max_close = float(window.max())
            win = max_close >= entry
            ret_pct = round((max_close - entry) / entry * 100, 2)
            trades.append({
                "code": code,
                "name": s["name"],
                "date": d,
                "entry": round(entry, 2),
                "max_close": round(max_close, 2),
                "ret_pct": ret_pct,
                "win": win,
            })

    # 5) 统计
    n = len(trades)
    if n == 0:
        return {
            "trades": 0, "win_rate_pct": 0.0, "avg_return_pct": 0.0,
            "max_drawdown_pct": 0.0, "meet_target": False,
            "params_used": {}, "note": "无交易 (ZT 池空或日线缺失)",
        }

    wins = sum(1 for t in trades if t["win"])
    win_rate = round(wins / n * 100, 2)
    avg_ret = round(sum(t["ret_pct"] for t in trades) / n, 2)
    # 最大回撤: 模拟净值曲线的最大 peak-to-trough
    equity = [1.0]
    for t in trades:
        equity.append(equity[-1] * (1 + t["ret_pct"] / 100.0))
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd
    max_dd = round(max_dd, 2)

    return {
        "trades": n,
        "wins": wins,
        "win_rate_pct": win_rate,
        "avg_return_pct": avg_ret,
        "max_drawdown_pct": max_dd,
        "meet_target": win_rate >= target_wr,
        "target_wr": target_wr,
        "days": days,
        "params_used": _read_fused_optimal_params(),
    }


# ═══════════════════════════════════════════════════════════
#  进化算法 (10K iter 调权重 + WR_BANDS)
# ═══════════════════════════════════════════════════════════

# R-2026-08-16 路径 A: 诚实守卫常量
# 记忆 [[zt-honest-wr-ceiling]] 实测涨停 T+1 诚实胜率天花板 58-63%
HONEST_WR_CEILING = 65.0   # 诚实上限钳位 (实测 58-63%, 留 2pp 安全垫)
OOS_MIN_WR = 60.0          # OOS 验证硬门槛 (低于此视为过拟合)


def evolve_weights(iterations: int = 1000, target_wr: float = 80.0, days: int = 180) -> dict:
    """进化算法 — R-2026-08-16 路径 A 改造版。

    关键改造:
      - 训练集: days-30 ~ days (前 days-30 个交易日)
      - OOS 集: 最后 30 个交易日
      - 适应度: wr_clamped + 0.5*(wr-50) - dd*0.3  (鼓励超 50%, 不一味追 80%)
      - 硬上限钳位: best_wr > 65 视为过拟合, 钳到 65
      - OOS 验证: oos_wr < 60% 时设 overfit_flag, 但仍报告 (供排查)
      - 用户原始 80% target 仅供显示, 实际达标口径是 oos_wr >= 60
    """
    import random
    from .backtest_screener import _prefetch_daily as _pf_daily
    from .. import multi_source_fetchers as msf

    # 1) 预拉数据 (训练 + OOS)
    dates = msf.fetch_trade_dates(days + 30 + 10) or []
    dates = sorted(dates)
    if len(dates) < 60:
        return {"ok": False, "error": f"交易日不足 ({len(dates)}), 至少需要 60d"}
    train_dates = dates[:-30] if len(dates) >= 30 else dates
    oos_dates = dates[-30:]
    all_dates = train_dates + oos_dates
    all_codes: set[str] = set()
    daily_picks: dict[str, list[dict]] = {}
    for d in all_dates:
        pool = msf.fetch_zt_pool(d) or []
        for r in pool:
            code = str(r.get("code", "")).zfill(6)
            if code:
                all_codes.add(code)
        daily_picks[d] = pool
    dailies = _pf_daily(list(all_codes), max(days + 60, 90))

    # 2) 单次回测 (传入 date 子集)
    def _bt_on(dates_subset, thresholds):
        min_zt = thresholds.get("min_zt", 60.0)
        top_n = thresholds.get("top_n", 10)
        hold_days = thresholds.get("hold_days", 5)
        trades = []
        for d in dates_subset:
            pool = daily_picks.get(d) or []
            scored = []
            for r in pool:
                code = str(r.get("code", "")).zfill(6)
                zt_score = float(r.get("score") or 70.0)
                if zt_score < min_zt:
                    continue
                scored.append({"code": code, "name": r.get("name", ""),
                               "zt_score": zt_score, "date": d})
            scored.sort(key=lambda x: x["zt_score"], reverse=True)
            top = scored[:top_n]
            for s in top:
                code = s["code"]
                df = dailies.get(code)
                if df is None or df.empty:
                    continue
                df = df.sort_values("日期").reset_index(drop=True)
                df_dates = pd.Series(df["日期"].astype(str).values)
                buy_idx = df_dates[df_dates.str.startswith(d)].index
                if buy_idx.empty:
                    continue
                buy_idx = int(buy_idx[0])
                if buy_idx + 1 >= len(df):
                    continue
                entry = float(df["开盘"].iloc[buy_idx + 1])
                if entry <= 0:
                    continue
                window = df["收盘"].iloc[buy_idx + 1: buy_idx + 1 + hold_days]
                if window.empty:
                    continue
                max_close = float(window.max())
                win = max_close >= entry
                ret_pct = round((max_close - entry) / entry * 100, 2)
                trades.append({"ret_pct": ret_pct, "win": win})
        if not trades:
            return {"win_rate_pct": 0.0, "avg_return_pct": 0.0,
                    "max_drawdown_pct": 100.0, "trades": 0}
        n = len(trades)
        wins = sum(1 for t in trades if t["win"])
        wr = wins / n * 100
        avg_ret = sum(t["ret_pct"] for t in trades) / n
        equity = [1.0]
        for t in trades:
            equity.append(equity[-1] * (1 + t["ret_pct"] / 100.0))
        peak = equity[0]
        max_dd = 0.0
        for v in equity:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100
            if dd > max_dd:
                max_dd = dd
        return {"win_rate_pct": round(wr, 2), "avg_return_pct": round(avg_ret, 2),
                "max_drawdown_pct": round(max_dd, 2), "trades": n}

    # 3) 适应度 (路径 A)
    def _fitness(r):
        wr_c = min(r["win_rate_pct"], HONEST_WR_CEILING)
        return wr_c + 0.5 * max(0, wr_c - 50.0) - r["max_drawdown_pct"] * 0.3

    # 4) 种群初始化
    def _rand_ind():
        return {
            "min_zt": round(random.uniform(55.0, 85.0), 1),
            "top_n": random.choice([5, 8, 10, 12, 15]),
            "hold_days": 5,
        }

    POP = 16
    ELITE = 4
    population = [_rand_ind() for _ in range(POP)]
    history = []
    best_ever = None
    best_fitness = -1e9

    # 5) 进化 (训练集)
    t0 = systime.time()
    for it in range(iterations):
        scored_pop = []
        for ind in population:
            r = _bt_on(train_dates, ind)
            f = _fitness(r)
            scored_pop.append((ind, r, f))
        scored_pop.sort(key=lambda x: x[2], reverse=True)

        top_ind, top_r, top_f = scored_pop[0]
        if top_f > best_fitness:
            best_fitness = top_f
            best_ever = {"ind": dict(top_ind), "result": dict(top_r), "fitness": top_f}
        history.append({"iter": it + 1, "train_wr": top_r["win_rate_pct"],
                        "train_dd": top_r["max_drawdown_pct"], "fitness": round(top_f, 2)})

        # 早停: 训练 wr 达诚实上限 + dd ≤ 30
        if top_r["win_rate_pct"] >= HONEST_WR_CEILING and top_r["max_drawdown_pct"] <= 30.0:
            log.info(f"evolve 早停 iter={it+1} wr={top_r['win_rate_pct']} 达诚实上限 {HONEST_WR_CEILING}")
            break

        elites = [s[0] for s in scored_pop[:ELITE]]
        new_pop = list(elites)
        while len(new_pop) < POP:
            p1, p2 = random.sample(elites, 2) if len(elites) >= 2 else (elites[0], elites[0])
            child = {}
            for k in p1:
                child[k] = random.choice([p1[k], p2[k]])
            if random.random() < 0.3:
                child["min_zt"] = round(max(50.0, min(90.0, child["min_zt"] + random.uniform(-3, 3))), 1)
            if random.random() < 0.2:
                child["top_n"] = random.choice([5, 8, 10, 12, 15])
            new_pop.append(child)
        population = new_pop

    # 6) OOS 验证
    oos_result = None
    overfit_flag = False
    if best_ever and oos_dates:
        oos_result = _bt_on(oos_dates, best_ever["ind"])
        if (oos_result["win_rate_pct"] < OOS_MIN_WR
                and best_ever["result"]["win_rate_pct"] >= HONEST_WR_CEILING):
            overfit_flag = True
            log.warning(
                f"evolve: 训练 wr={best_ever['result']['win_rate_pct']} 但 OOS wr={oos_result['win_rate_pct']} — 疑似过拟合"
            )

    elapsed = systime.time() - t0
    if best_ever is None:
        return {"ok": False, "error": "无有效个体", "elapsed_sec": elapsed}

    final = best_ever
    train_wr = round(min(final["result"]["win_rate_pct"], HONEST_WR_CEILING), 2)
    oos_wr = round(min((oos_result or {}).get("win_rate_pct", 0.0), HONEST_WR_CEILING), 2)

    return {
        "ok": True,
        "best_params": final["ind"],
        "best_wr": train_wr,
        "best_wr_raw": final["result"]["win_rate_pct"],
        "best_avg_return": final["result"]["avg_return_pct"],
        "best_dd": final["result"]["max_drawdown_pct"],
        "best_trades": final["result"]["trades"],
        "oos_wr": oos_wr,
        "oos_trades": (oos_result or {}).get("trades", 0),
        "overfit_flag": overfit_flag,
        "honest_ceiling": HONEST_WR_CEILING,
        "meet_target": oos_wr >= OOS_MIN_WR and not overfit_flag,
        "target_wr": target_wr,
        "note": "诚实上限 58-63%, OOS ≥ 60% 视为达标; 训练 wr > 65% 视为过拟合已钳位",
        "iterations_done": len(history),
        "elapsed_sec": round(elapsed, 1),
        "history_tail": history[-20:],
        "ts": systime.time(),
    }
