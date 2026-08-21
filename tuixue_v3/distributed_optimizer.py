#!/usr/bin/env python3
"""
tuixue_v3/distributed_optimizer.py
Ship 10/100 — 分布式参数优化器 (Redis SortedSet 工作队列)

问题: zt_optimizer 1000 轮参数搜索是单进程串行, 一轮回测 ~2s → 30+ 分钟。
方案: 把参数网格拆成 task 丢进 Redis ZSet, 多 worker 并行认领, 结果回写排行榜。

设计:
- 任务队列: ZSet, score = 优先级 (小的先跑), member = task_id
- 认领互斥: set_nx 租约 (lease), TTL 到期自动回到待认领 — worker 崩了任务不丢
- 结果排行: ZSet, score = 回测得分, member = task_id
- 无 Redis 时 cache_store 自动降级 SQLite, 单机多进程照样能跑

关键取舍: 用 "ZSet + set_nx 租约" 而不是 Redis Stream/BLPOP, 因为 cache_store
只暴露了 zadd/zrange/set_nx 这几个原语, 且租约模式天然处理 worker 崩溃。

2026-08-02 Ship 10 — 10000 轮迭代 P1 第五步
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════

QUEUE_KEY = "optq:pending"
RESULT_KEY = "optq:results"
LEASE_PREFIX = "optq:lease:"
TASK_PREFIX = "optq:task:"
DONE_PREFIX = "optq:done:"

DEFAULT_LEASE_TTL = 300       # worker 崩溃后 5 分钟任务回池
DEFAULT_RESULT_TTL = 86400    # 结果留 1 天


def _worker_id() -> str:
    """worker 唯一标识 — 主机名 + pid + 随机后缀 (同机多进程不撞)"""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class OptTask:
    """单个待评估的参数组合"""
    task_id: str
    params: dict = field(default_factory=dict)
    priority: float = 0.0

    def to_dict(self) -> dict:
        return {"task_id": self.task_id, "params": self.params,
                "priority": self.priority}

    @staticmethod
    def from_dict(d: dict) -> "OptTask":
        return OptTask(
            task_id=str(d.get("task_id", "")),
            params=d.get("params") or {},
            priority=float(d.get("priority", 0.0)),
        )


@dataclass
class OptResult:
    """单次评估结果"""
    task_id: str
    score: float = 0.0
    params: dict = field(default_factory=dict)
    worker: str = ""
    elapsed_ms: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return {"task_id": self.task_id, "score": self.score,
                "params": self.params, "worker": self.worker,
                "elapsed_ms": self.elapsed_ms, "error": self.error}

    @staticmethod
    def from_dict(d: dict) -> "OptResult":
        return OptResult(
            task_id=str(d.get("task_id", "")),
            score=float(d.get("score", 0.0)),
            params=d.get("params") or {},
            worker=str(d.get("worker", "")),
            elapsed_ms=float(d.get("elapsed_ms", 0.0)),
            error=str(d.get("error", "")),
        )


# ═══════════════════════════════════════════════════════
# 参数网格展开
# ═══════════════════════════════════════════════════════

def expand_grid(grid: dict[str, list]) -> list[dict]:
    """参数网格 → 笛卡尔积参数组合列表

    >>> expand_grid({"a": [1, 2], "b": ["x"]})
    [{'a': 1, 'b': 'x'}, {'a': 2, 'b': 'x'}]
    """
    if not grid:
        return []
    keys = list(grid.keys())
    value_lists = [grid[k] for k in keys]
    if any(not v for v in value_lists):
        return []
    return [dict(zip(keys, combo)) for combo in itertools.product(*value_lists)]


def make_task_id(params: dict) -> str:
    """参数 → 稳定 task_id

    用排序后 JSON 的 hash, 保证同参数组合在任何 worker 上算出同一个 id,
    重复提交天然幂等 (ZSet member 去重)。
    """
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
    import hashlib
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]


# ═══════════════════════════════════════════════════════
# 分布式优化器
# ═══════════════════════════════════════════════════════

class DistributedOptimizer:
    """Redis ZSet 工作队列 — 多 worker 并行参数搜索"""

    def __init__(self, run_id: str, *, store=None,
                 lease_ttl: int = DEFAULT_LEASE_TTL,
                 result_ttl: int = DEFAULT_RESULT_TTL):
        self.run_id = run_id
        self.lease_ttl = lease_ttl
        self.result_ttl = result_ttl
        self.worker = _worker_id()
        self._store = store

    @property
    def store(self):
        if self._store is None:
            from tuixue_v3 import cache_store
            self._store = cache_store.get_store()
        return self._store

    # ── key helpers ──
    def _qk(self) -> str:
        return f"{QUEUE_KEY}:{self.run_id}"

    def _rk(self) -> str:
        return f"{RESULT_KEY}:{self.run_id}"

    def _lease_k(self, task_id: str) -> str:
        return f"{LEASE_PREFIX}{self.run_id}:{task_id}"

    def _task_k(self, task_id: str) -> str:
        return f"{TASK_PREFIX}{self.run_id}:{task_id}"

    def _done_k(self, task_id: str) -> str:
        return f"{DONE_PREFIX}{self.run_id}:{task_id}"

    # ── 提交 ──
    def submit(self, params_list: list[dict], *, priority: float = 0.0) -> list[OptTask]:
        """批量提交参数组合, 返回创建的 task 列表 (幂等: 同参数不重复入队)"""
        tasks = []
        for i, p in enumerate(params_list):
            t = OptTask(task_id=make_task_id(p), params=p, priority=priority + i)
            self.store.set(self._task_k(t.task_id), t.to_dict(), ttl=self.result_ttl)
            self.store.zadd(self._qk(), t.priority, t.task_id, ttl=self.result_ttl)
            tasks.append(t)
        logger.info("DistributedOptimizer[%s]: 提交 %d 任务", self.run_id, len(tasks))
        return tasks

    def submit_grid(self, grid: dict[str, list]) -> list[OptTask]:
        """参数网格直接提交"""
        return self.submit(expand_grid(grid))

    # ── 认领 ──
    def claim(self) -> Optional[OptTask]:
        """认领一个待办任务 — 租约互斥, 返回 None 表示队列空

        遍历 ZSet 按优先级取第一个能拿到租约的任务。已完成的顺手清出队列,
        避免每次认领都重扫一遍历史任务。
        """
        pending = self.store.zrange(self._qk(), 0, -1)
        for _score, task_id in pending:
            if not isinstance(task_id, str):
                continue
            if self.store.exists(self._done_k(task_id)):
                self.store.zremrangebyscore(self._qk(), _score, _score)
                continue
            if not self.store.set_nx(self._lease_k(task_id), self.worker,
                                     ttl=self.lease_ttl):
                continue  # 别的 worker 正在跑
            d = self.store.get(self._task_k(task_id))
            if not isinstance(d, dict):
                self.store.delete(self._lease_k(task_id))
                continue
            return OptTask.from_dict(d)
        return None

    # ── 回写 ──
    def complete(self, result: OptResult) -> bool:
        """回写结果 — 写排行榜 + 标记完成 + 释放租约 + 从待办 ZSet 移除"""
        result.worker = result.worker or self.worker
        self.store.set(self._done_k(result.task_id), result.to_dict(),
                       ttl=self.result_ttl)
        self.store.zadd(self._rk(), result.score, result.task_id,
                        ttl=self.result_ttl)
        self.store.delete(self._lease_k(result.task_id))
        # 从待办 ZSet 清掉, 否则 progress 永远算 pending — claim 兜底扫描
        # 是兜底, 不能依赖下次有人来捞才更新计数
        all_rows = self.store.zrange(self._qk(), 0, -1)
        for score, task_id in all_rows:
            if task_id == result.task_id:
                self.store.zremrangebyscore(self._qk(), score, score)
                break
        return True

    def fail(self, task_id: str, error: str) -> bool:
        """标记失败 — 释放租约 + 从待办 ZSet 移除 + 写失败标记

        失败任务不写排行榜 (score=-1 会被 best() 选上), 写 done 标记但 score=0
        + error 字段, progress 视为 done 但 leaderboard 跳过。
        """
        # 查 task 的 score, 从待办 ZSet 移除
        for s, tid in self.store.zrange(self._qk(), 0, -1):
            if tid == task_id:
                self.store.zremrangebyscore(self._qk(), s, s)
                break
        # 写 done 标记 + 写结果 ZSet (score=0, 标识失败) — progress 靠 ZSet 计数
        self.store.set(self._done_k(task_id), {
            "task_id": task_id, "score": 0.0, "params": {},
            "worker": self.worker, "elapsed_ms": 0.0,
            "error": error[:500], "failed": True,
        }, ttl=self.result_ttl)
        self.store.zadd(self._rk(), 0.0, task_id, ttl=self.result_ttl)
        self.store.delete(self._lease_k(task_id))
        logger.warning("DistributedOptimizer[%s]: task %s 失败: %s",
                       self.run_id, task_id, error[:200])
        return True

    # ── 查询 ──
    def leaderboard(self, top_n: int = 10) -> list[OptResult]:
        """得分排行榜 (高分在前, 失败任务排除)"""
        rows = self.store.zrange(self._rk(), 0, -1)
        rows.sort(key=lambda r: r[0], reverse=True)
        out = []
        for _score, task_id in rows:
            if not isinstance(task_id, str):
                continue
            d = self.store.get(self._done_k(task_id))
            if not isinstance(d, dict):
                continue
            if d.get("failed"):
                continue
            out.append(OptResult.from_dict(d))
            if len(out) >= top_n:
                break
        return out

    def best(self) -> Optional[OptResult]:
        """最优结果"""
        top = self.leaderboard(top_n=1)
        return top[0] if top else None

    def progress(self) -> dict:
        """进度 — total / done / pending / running

        total = 待办 ZSet (含已完成的) ∪ 结果 ZSet
        完成后任务仍在待办 ZSet (claim 扫到时再清), 进度按两者并集算
        """
        pending_rows = self.store.zrange(self._qk(), 0, -1)
        result_rows = self.store.zrange(self._rk(), 0, -1)
        all_ids = {t for _, t in pending_rows + result_rows if isinstance(t, str)}
        total = len(all_ids)
        done = len(result_rows)
        running = 0
        for tid in all_ids:
            if tid in {t for _, t in result_rows}:
                continue
            if self.store.exists(self._lease_k(tid)):
                running += 1
        return {
            "run_id": self.run_id,
            "total": total,
            "done": done,
            "running": running,
            "pending": max(0, total - done - running),
            "pct": round(done / total * 100, 2) if total else 0.0,
        }

    # ── worker 主循环 ──
    def run_worker(self, evaluate: Callable[[dict], float], *,
                   max_tasks: int = 0, idle_exit: bool = True) -> int:
        """worker 主循环 — 不断认领任务并评估

        Args:
            evaluate: 参数 dict → 得分 float 的回测函数
            max_tasks: 最多跑几个任务, 0 = 不限
            idle_exit: 队列空时退出 (False 则轮询等待)

        Returns:
            完成 (成功或失败) 的任务数

        失败处理: fail() 落 done 标记 (failed=True) 并出池, worker 不在
        本进程内重试 — 避免坏参数格在 worker 内死循环。调用方如果想重试
        failed 任务, 手动 submit 一次即可。
        """
        n = 0
        while True:
            if max_tasks and n >= max_tasks:
                break
            task = self.claim()
            if task is None:
                if idle_exit:
                    break
                time.sleep(1.0)
                continue
            t0 = time.monotonic()
            try:
                score = float(evaluate(task.params))
            except Exception as e:
                self.fail(task.task_id, str(e))
                n += 1
                continue
            self.complete(OptResult(
                task_id=task.task_id, score=score, params=task.params,
                worker=self.worker,
                elapsed_ms=round((time.monotonic() - t0) * 1000, 2),
            ))
            n += 1
        return n

    def clear(self) -> None:
        """清空本次 run 的所有状态 (测试 / 重跑用)"""
        all_task_ids: set[str] = set()
        for _s, task_id in self.store.zrange(self._qk(), 0, -1):
            if isinstance(task_id, str):
                all_task_ids.add(task_id)
        for _s, task_id in self.store.zrange(self._rk(), 0, -1):
            if isinstance(task_id, str):
                all_task_ids.add(task_id)
        for tid in all_task_ids:
            self.store.delete(self._task_k(tid))
            self.store.delete(self._done_k(tid))
            self.store.delete(self._lease_k(tid))
        # 清 ZSet 本身 (不是删 key, 是清空 member)
        if hasattr(self.store, "zdelete"):
            self.store.zdelete(self._qk())
            self.store.zdelete(self._rk())
        else:
            # Redis 退化路径: 用 zremrangebyscore 扫很大范围清空
            self.store.zremrangebyscore(self._qk(), float("-inf"), float("inf"))
            self.store.zremrangebyscore(self._rk(), float("-inf"), float("inf"))
