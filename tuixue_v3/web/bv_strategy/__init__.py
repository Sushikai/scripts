"""
BV 战法 (Bryan交易随笔 — 仓位管理战法)

骨架 (R2001.1 / SW v618, 2026-08-17):
  - 从 data/bv_rules.json 加载战法规则
  - /api/bv/{rules, live_pick, scan, backtest, meta} 5 个端点
  - L0 inproc 30s + L1 Redis 60s + L3 stale 1800s 三层缓存
  - Phase 判断: pre_market / early / midday / late_afternoon / closing / close

路由表:
  GET /api/bv/rules          — 战法规则明细 (含原话引用 + 时间戳)
  GET /api/bv/meta           — 战法 meta (名称/版本/更新时间/规则数)
  GET /api/bv/live_pick      — 实时推票 (top_n)
  GET /api/bv/scan           — 全市场扫描 (refresh / top_n)
  GET /api/bv/backtest       — 历史回测 (days)

后续轮次:
  R2002.x: screener 全市场扫描
  R2003.x: backtest 历史回测真实数据
  R2004.x: phase-aware UI 策略
  R2005.x: 战法规则 v2 (LLM 重新提炼)
"""

import asyncio
import logging
import time
from starlette.concurrency import run_in_threadpool as to_thread

log = logging.getLogger("tuixue.bv")

__version__ = "0.1.0-r2001.5"
__all__ = ["register"]


def register(app):
    """挂载 BV 战法 5 个路由到 FastAPI app。"""
    # ═══════════════════════════════════════════════════════════
    #  L1 Redis 跨 worker 缓存
    # ═══════════════════════════════════════════════════════════
    def _redis_get(key: str) -> dict | None:
        try:
            from .. import cache_store as _cs
            s = _cs.get_store()
            if s:
                return s.get(key)
        except Exception:
            pass
        return None

    def _redis_set(key: str, data: dict, ttl: float) -> None:
        try:
            from .. import cache_store as _cs
            s = _cs.get_store()
            if s:
                s.set(key, data, ttl=ttl)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    #  GET /api/bv/meta — 战法元信息
    # ═══════════════════════════════════════════════════════════
    @app.get("/api/bv/meta")
    async def api_bv_meta(refresh: bool = False):
        """战法 meta: 名称/UP主/版本/规则数/摘要/bvid。
        缓存: L1 Redis 60min + L3 stale 60min。
        """
        from .rules import get_meta, load_rules
        from .realtime import phase_meta

        key = "bv_meta"
        if not refresh:
            blob = _redis_get(key)
            if blob:
                blob["_cache_hit"] = "redis"
                return blob

        meta = get_meta()
        rules = load_rules().get("rules", [])
        phase = phase_meta()
        body = {
            "ok": True,
            "data": {
                "name": meta.get("name", ""),
                "up": meta.get("up", ""),
                "version": meta.get("version", "v1"),
                "rule_count": meta.get("rule_count", 0),
                "summary": meta.get("summary", ""),
                "extracted_at": meta.get("extracted_at", ""),
                "bvid": meta.get("bvid", ""),
                "quote_corpus": load_rules().get("quote_corpus", []),
                "philosophy": load_rules().get("philosophy", []),
                "phase": phase,
                "ts": time.time(),
            },
            "ts": time.time(),
        }
        _redis_set(key, body, ttl=3600.0)
        return body

    # ═══════════════════════════════════════════════════════════
    #  GET /api/bv/rules — 战法规则明细
    # ═══════════════════════════════════════════════════════════
    @app.get("/api/bv/rules")
    async def api_bv_rules(refresh: bool = False, category: str | None = None):
        """规则明细 — 可选按 category 过滤。
        缓存: L1 Redis 24h + L3 stale 24h (规则改动需重启 server)。
        """
        from .rules import load_rules, get_rules_by_category

        key = "bv_rules"
        if not refresh:
            blob = _redis_get(key)
            if blob:
                blob["_cache_hit"] = "redis"
                if category:
                    blob["data"]["rules"] = [
                        r for r in blob["data"]["rules"] if r.get("category") == category
                    ]
                return blob

        rules = load_rules().get("rules", [])
        if category:
            rules = [r for r in rules if r.get("category") == category]

        body = {
            "ok": True,
            "data": {
                "name": load_rules().get("name", ""),
                "version": load_rules().get("version", "v1"),
                "rules": rules,
                "by_category": {
                    cat: [{"id": r["id"], "title": r["title"], "score_weight": r.get("score_weight", 0), "priority": r.get("priority", 99)}
                          for r in items]
                    for cat, items in get_rules_by_category().items()
                },
                "ts": time.time(),
            },
            "ts": time.time(),
        }
        _redis_set(key, body, ttl=86400.0)
        return body

    # ═══════════════════════════════════════════════════════════
    #  GET /api/bv/live_pick — 实时推票
    # ═══════════════════════════════════════════════════════════
    @app.get("/api/bv/live_pick")
    async def api_bv_live_pick(top_n: int = 15, refresh: bool = False):
        """实时推票 (盘中 30s / 盘后 60s 暖路径)。

        缓存: L0 inproc 30s + L1 Redis 60s + L3 stale 1800s。
        失败兜底: 上游断时返 stale + _degraded 标志。
        """
        from .screener import live_pick_sync

        if not refresh:
            blob = _redis_get("bv_live_pick")
            if blob and isinstance(blob.get("data"), dict):
                blob["_cache_hit"] = "redis"
                return blob

        try:
            data = live_pick_sync(top_n=top_n, refresh=refresh)
        except Exception as e:
            log.exception(f"bv_live_pick 失败: {e}")
            data = {"ts": time.time(), "scanned": 0, "matched": 0, "top_n": top_n, "picks": [], "phase": "close", "_error": str(e)}

        # 包装 envelope + envelope_degraded
        from .realtime import phase_meta
        phase = phase_meta()
        data["phase"] = phase["phase"]
        data["phase_label"] = phase["label"]
        data["phase_ttl"] = phase["ttl"]

        body = {
            "ok": True,
            "data": data,
            "ts": time.time(),
        }

        # 暖路径写 Redis + L3 stale
        if data.get("matched", 0) > 0:
            _redis_set("bv_live_pick", body, ttl=60.0)

        # L3 stale 兜底 — 延迟 import 避免循环依赖
        try:
            import sys as _sys
            _srv = _sys.modules.get("web.server")
            if _srv is not None and hasattr(_srv, "_stale_save"):
                _srv._stale_save("bv_live_pick", body)
        except Exception:
            pass

        return body

    # ═══════════════════════════════════════════════════════════
    #  GET /api/bv/scan — 全市场扫描 (跟 live_pick 同算法但无 top_n 限制)
    # ═══════════════════════════════════════════════════════════
    @app.get("/api/bv/scan")
    async def api_bv_scan(refresh: bool = False, top_n: int = 50):
        """全市场扫描 — 同 live_pick 算法, 但 top_n 默认 50。

        用于前端"展开全部" 按钮 / 详情面板。
        """
        from .screener import live_pick_sync

        key = f"bv_scan:{top_n}"
        if not refresh:
            blob = _redis_get(key)
            if blob and isinstance(blob.get("data"), dict):
                blob["_cache_hit"] = "redis"
                return blob

        try:
            data = live_pick_sync(top_n=top_n, refresh=refresh)
        except Exception as e:
            log.exception(f"bv_scan 失败: {e}")
            data = {"ts": time.time(), "scanned": 0, "matched": 0, "top_n": top_n, "picks": [], "phase": "close", "_error": str(e)}

        body = {"ok": True, "data": data, "ts": time.time()}
        if data.get("matched", 0) > 0:
            _redis_set(key, body, ttl=120.0)
        return body

    # ═══════════════════════════════════════════════════════════
    #  GET /api/bv/backtest — 历史回测
    # ═══════════════════════════════════════════════════════════
    @app.get("/api/bv/backtest")
    async def api_bv_backtest(days: int = 180, refresh: bool = False):
        """历史回测 — 骨架版返 status=skeleton, R2003.x 接入真实数据。

        缓存: L1 Redis 24h + L3 stale 24h。
        R2000.61 (2026-08-22): asyncio.wait_for 90s 硬超时 — fetch_daily 串行 10+ code
        实际跑 80s+, 没 asyncio 兜底会被 hypercorn 客户端超时切断。
        """
        from .backtest import bv_backtest
        import asyncio

        key = f"bv_backtest:{days}"
        if not refresh:
            blob = _redis_get(key)
            if blob and isinstance(blob.get("data"), dict):
                blob["_cache_hit"] = "redis"
                return blob

        try:
            data = await asyncio.wait_for(
                to_thread(bv_backtest, days=days, refresh=refresh),
                timeout=90.0,
            )
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "error": "请求超时 90s — 上游 fetch_daily 串行 10+ code 拖垮, 请稍后重试或减小 days",
                "data": {"status": "timeout_backtest"},
                "ts": time.time(),
            }
        body = {"ok": True, "data": data, "ts": time.time()}
        _redis_set(key, body, ttl=86400.0)
        return body