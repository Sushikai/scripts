#!/usr/bin/env python3
"""
test_distributed_optimizer.py
Ship 10 单元测试 — 分布式参数优化器 (Redis ZSet 工作队列)

用内存 FakeStore 替 cache_store, 不依赖真 Redis。
"""
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.distributed_optimizer import (
    DistributedOptimizer, OptTask, OptResult,
    expand_grid, make_task_id,
)


class FakeStore:
    """内存版 cache_store — 只实现优化器用到的原语, 带锁模拟原子性"""

    def __init__(self):
        self._kv = {}
        self._z = {}
        self._lock = threading.Lock()

    def set(self, key, value, ttl=0):
        with self._lock:
            self._kv[key] = value
        return True

    def get(self, key):
        return self._kv.get(key)

    def delete(self, key):
        with self._lock:
            return self._kv.pop(key, None) is not None

    def exists(self, key):
        return key in self._kv

    def set_nx(self, key, value, ttl=0):
        with self._lock:
            if key in self._kv:
                return False
            self._kv[key] = value
            return True

    def zadd(self, key, score, value, ttl=0):
        with self._lock:
            self._z.setdefault(key, {})[value] = score
        return True

    def zrange(self, key, start=0, end=-1):
        items = sorted(self._z.get(key, {}).items(), key=lambda kv: kv[1])
        rows = [(s, v) for v, s in items]
        return rows if end == -1 else rows[start:end + 1]

    def zremrangebyscore(self, key, min_, max_):
        with self._lock:
            z = self._z.get(key, {})
            drop = [v for v, s in z.items() if min_ <= s <= max_]
            for v in drop:
                del z[v]
            return len(drop)

    def zdelete(self, key: str) -> bool:
        with self._lock:
            return self._z.pop(key, None) is not None


def make_opt(run_id="t1"):
    return DistributedOptimizer(run_id, store=FakeStore())


class TestExpandGrid(unittest.TestCase):
    """参数网格展开"""

    def test_cartesian(self):
        out = expand_grid({"a": [1, 2], "b": ["x", "y"]})
        self.assertEqual(len(out), 4)
        self.assertIn({"a": 1, "b": "x"}, out)
        self.assertIn({"a": 2, "b": "y"}, out)

    def test_single_key(self):
        self.assertEqual(expand_grid({"a": [1, 2, 3]}),
                         [{"a": 1}, {"a": 2}, {"a": 3}])

    def test_empty_grid(self):
        self.assertEqual(expand_grid({}), [])

    def test_empty_value_list_yields_nothing(self):
        self.assertEqual(expand_grid({"a": [1], "b": []}), [])

    def test_large_grid(self):
        out = expand_grid({"a": list(range(10)), "b": list(range(10))})
        self.assertEqual(len(out), 100)


class TestMakeTaskId(unittest.TestCase):
    """task_id 稳定性"""

    def test_deterministic(self):
        self.assertEqual(make_task_id({"a": 1}), make_task_id({"a": 1}))

    def test_key_order_irrelevant(self):
        """dict 顺序不同但内容相同 → 同 id (幂等去重的前提)"""
        self.assertEqual(make_task_id({"a": 1, "b": 2}),
                         make_task_id({"b": 2, "a": 1}))

    def test_different_params_differ(self):
        self.assertNotEqual(make_task_id({"a": 1}), make_task_id({"a": 2}))

    def test_length(self):
        self.assertEqual(len(make_task_id({"a": 1})), 16)


class TestDataclasses(unittest.TestCase):
    """OptTask / OptResult 往返"""

    def test_task_roundtrip(self):
        t = OptTask(task_id="x", params={"a": 1}, priority=3.0)
        self.assertEqual(OptTask.from_dict(t.to_dict()), t)

    def test_result_roundtrip(self):
        r = OptResult(task_id="x", score=1.5, params={"a": 1}, worker="w")
        self.assertEqual(OptResult.from_dict(r.to_dict()), r)

    def test_from_dict_missing_fields(self):
        r = OptResult.from_dict({})
        self.assertEqual(r.task_id, "")
        self.assertEqual(r.score, 0.0)


class TestSubmit(unittest.TestCase):
    """提交"""

    def test_submit_count(self):
        o = make_opt()
        tasks = o.submit([{"a": 1}, {"a": 2}])
        self.assertEqual(len(tasks), 2)
        self.assertEqual(o.progress()["total"], 2)

    def test_submit_grid(self):
        o = make_opt()
        o.submit_grid({"a": [1, 2], "b": [3]})
        self.assertEqual(o.progress()["total"], 2)

    def test_idempotent(self):
        """同参数重复提交不该膨胀队列"""
        o = make_opt()
        o.submit([{"a": 1}])
        o.submit([{"a": 1}])
        self.assertEqual(o.progress()["total"], 1)

    def test_submit_empty(self):
        o = make_opt()
        self.assertEqual(o.submit([]), [])


class TestClaim(unittest.TestCase):
    """认领 + 租约互斥"""

    def test_claim_returns_task(self):
        o = make_opt()
        o.submit([{"a": 1}])
        t = o.claim()
        self.assertIsNotNone(t)
        self.assertEqual(t.params, {"a": 1})

    def test_claim_empty_queue(self):
        self.assertIsNone(make_opt().claim())

    def test_lease_blocks_second_worker(self):
        """同一任务不能被两个 worker 同时认领"""
        store = FakeStore()
        a = DistributedOptimizer("r", store=store)
        b = DistributedOptimizer("r", store=store)
        a.submit([{"x": 1}])
        self.assertIsNotNone(a.claim())
        self.assertIsNone(b.claim())

    def test_two_tasks_two_workers(self):
        store = FakeStore()
        a = DistributedOptimizer("r", store=store)
        b = DistributedOptimizer("r", store=store)
        a.submit([{"x": 1}, {"x": 2}])
        ta, tb = a.claim(), b.claim()
        self.assertIsNotNone(ta)
        self.assertIsNotNone(tb)
        self.assertNotEqual(ta.task_id, tb.task_id)

    def test_priority_order(self):
        """低 priority 先出队"""
        o = make_opt()
        o.submit([{"a": 1}, {"a": 2}, {"a": 3}])
        self.assertEqual(o.claim().params, {"a": 1})

    def test_done_task_skipped(self):
        o = make_opt()
        t = o.submit([{"a": 1}])[0]
        o.complete(OptResult(task_id=t.task_id, score=1.0))
        self.assertIsNone(o.claim())


class TestComplete(unittest.TestCase):
    """回写"""

    def test_complete_records_result(self):
        o = make_opt()
        t = o.submit([{"a": 1}])[0]
        o.complete(OptResult(task_id=t.task_id, score=2.5, params={"a": 1}))
        best = o.best()
        self.assertIsNotNone(best)
        self.assertEqual(best.score, 2.5)

    def test_complete_releases_lease(self):
        """完成后租约释放, 但 done 标记阻止重复认领"""
        o = make_opt()
        t = o.submit([{"a": 1}])[0]
        o.claim()
        o.complete(OptResult(task_id=t.task_id, score=1.0))
        self.assertFalse(o.store.exists(o._lease_k(t.task_id)))

    def test_worker_auto_filled(self):
        o = make_opt()
        t = o.submit([{"a": 1}])[0]
        o.complete(OptResult(task_id=t.task_id, score=1.0))
        self.assertTrue(o.best().worker)


class TestFail(unittest.TestCase):
    """失败 — 落 done 标记不进回池, 避免坏参数死循环"""

    def test_fail_does_not_reclaim(self):
        """失败任务不再回池, 避免 worker 在坏参数上死循环"""
        o = make_opt()
        t = o.submit([{"a": 1}])[0]
        o.claim()
        o.fail(t.task_id, "boom")
        self.assertIsNone(o.claim())

    def test_fail_not_in_leaderboard(self):
        o = make_opt()
        t = o.submit([{"a": 1}])[0]
        o.claim()
        o.fail(t.task_id, "boom")
        # 失败任务进 result ZSet 但 leaderboard 跳过 failed 标记
        self.assertEqual(o.leaderboard(), [])

    def test_fail_with_success_in_leaderboard(self):
        """失败与成功共存时, 排行榜只列成功的"""
        o = make_opt()
        ts = o.submit([{"a": 1}, {"a": 2}])
        o.claim()
        o.fail(ts[0].task_id, "boom")
        o.complete(OptResult(task_id=ts[1].task_id, score=5.0))
        self.assertEqual([r.score for r in o.leaderboard()], [5.0])


class TestLeaderboard(unittest.TestCase):
    """排行榜"""

    def test_sorted_desc(self):
        o = make_opt()
        ts = o.submit([{"a": 1}, {"a": 2}, {"a": 3}])
        for t, s in zip(ts, [1.0, 5.0, 3.0]):
            o.complete(OptResult(task_id=t.task_id, score=s, params=t.params))
        lb = o.leaderboard()
        self.assertEqual([r.score for r in lb], [5.0, 3.0, 1.0])

    def test_top_n(self):
        o = make_opt()
        ts = o.submit([{"a": i} for i in range(5)])
        for i, t in enumerate(ts):
            o.complete(OptResult(task_id=t.task_id, score=float(i)))
        self.assertEqual(len(o.leaderboard(top_n=2)), 2)

    def test_empty(self):
        self.assertEqual(make_opt().leaderboard(), [])
        self.assertIsNone(make_opt().best())

    def test_negative_scores(self):
        o = make_opt()
        ts = o.submit([{"a": 1}, {"a": 2}])
        o.complete(OptResult(task_id=ts[0].task_id, score=-5.0))
        o.complete(OptResult(task_id=ts[1].task_id, score=-1.0))
        self.assertEqual(o.best().score, -1.0)


class TestProgress(unittest.TestCase):
    """进度"""

    def test_initial(self):
        o = make_opt()
        o.submit([{"a": 1}, {"a": 2}])
        p = o.progress()
        self.assertEqual((p["total"], p["done"], p["pending"]), (2, 0, 2))

    def test_running_counted(self):
        o = make_opt()
        o.submit([{"a": 1}, {"a": 2}])
        o.claim()
        p = o.progress()
        self.assertEqual(p["running"], 1)
        self.assertEqual(p["pending"], 1)

    def test_pct(self):
        o = make_opt()
        ts = o.submit([{"a": 1}, {"a": 2}, {"a": 3}, {"a": 4}])
        o.complete(OptResult(task_id=ts[0].task_id, score=1.0))
        self.assertEqual(o.progress()["pct"], 25.0)

    def test_empty_no_div_zero(self):
        self.assertEqual(make_opt().progress()["pct"], 0.0)


class TestRunWorker(unittest.TestCase):
    """worker 主循环"""

    def test_drains_queue(self):
        o = make_opt()
        o.submit_grid({"a": [1, 2, 3]})
        n = o.run_worker(lambda p: float(p["a"]))
        self.assertEqual(n, 3)
        self.assertEqual(o.progress()["done"], 3)
        self.assertEqual(o.best().params["a"], 3)

    def test_max_tasks(self):
        o = make_opt()
        o.submit_grid({"a": [1, 2, 3, 4, 5]})
        self.assertEqual(o.run_worker(lambda p: 1.0, max_tasks=2), 2)

    def test_evaluate_exception_does_not_kill_worker(self):
        """一个参数组合炸了不该让整个 worker 挂掉"""
        o = make_opt()
        o.submit_grid({"a": [1, 2, 3]})

        def evaluate(p):
            if p["a"] == 2:
                raise ValueError("bad param")
            return float(p["a"])

        n = o.run_worker(evaluate)
        # 3 个都被处理: 1 成功, 2 失败 (落 done), 3 成功
        self.assertEqual(n, 3)
        # 排行榜只算成功的 2 个
        self.assertEqual(len(o.leaderboard()), 2)
        # 失败的不在榜上
        self.assertEqual({r.params["a"] for r in o.leaderboard()}, {1, 3})

    def test_idle_exit_on_empty(self):
        self.assertEqual(make_opt().run_worker(lambda p: 1.0), 0)

    def test_two_workers_split_work(self):
        """两 worker 分摊, 每个任务恰好跑一次"""
        store = FakeStore()
        a = DistributedOptimizer("r", store=store)
        b = DistributedOptimizer("r", store=store)
        a.submit_grid({"a": list(range(10))})

        seen = []
        na = a.run_worker(lambda p: (seen.append(p["a"]), 1.0)[1], max_tasks=5)
        nb = b.run_worker(lambda p: (seen.append(p["a"]), 1.0)[1])

        self.assertEqual(na + nb, 10)
        self.assertEqual(sorted(seen), list(range(10)))

    def test_concurrent_workers_no_double_eval(self):
        """真并发: 4 线程抢 20 任务, 不能有任何任务被评估两次"""
        store = FakeStore()
        opts = [DistributedOptimizer("r", store=store) for _ in range(4)]
        opts[0].submit_grid({"a": list(range(20))})

        seen = []
        seen_lock = threading.Lock()

        def evaluate(p):
            with seen_lock:
                seen.append(p["a"])
            return float(p["a"])

        threads = [threading.Thread(target=o.run_worker, args=(evaluate,))
                   for o in opts]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(sorted(seen), list(range(20)))
        self.assertEqual(opts[0].progress()["done"], 20)


class TestClear(unittest.TestCase):
    """清空"""

    def test_clear_resets(self):
        o = make_opt()
        ts = o.submit_grid({"a": [1, 2]})
        o.complete(OptResult(task_id=ts[0].task_id, score=1.0))
        o.clear()
        self.assertEqual(o.progress()["total"], 0)
        self.assertEqual(o.leaderboard(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
