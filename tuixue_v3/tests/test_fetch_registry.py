#!/usr/bin/env python3
"""
test_data_source_registry.py
Ship 1 单元测试 — FetchRegistry 核心功能
"""
import sys
import os
import unittest
from pathlib import Path

# 添加 tuixue_v3 到 path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.data_source_registry import (
    FetchRegistry,
    FetchSource,
    FetchResult,
    registry,
    fetch_with_registry,
    register_source,
)


class TestFetchSourceDataclass(unittest.TestCase):
    """FetchSource dataclass 基本行为"""

    def test_create_minimal(self):
        s = FetchSource(name="x", category="daily", fn=lambda c: None)
        self.assertEqual(s.name, "x")
        self.assertEqual(s.category, "daily")
        self.assertEqual(s.priority, 100)
        self.assertEqual(s.timeout, 4.0)
        self.assertTrue(s.enabled)
        self.assertEqual(s.display_name, "x")  # 默认等于 name

    def test_post_init_default_display(self):
        s = FetchSource(name="x", display_name="", category="daily", fn=lambda c: None)
        self.assertEqual(s.display_name, "x")  # 自动回退到 name


class TestRegistryBasic(unittest.TestCase):
    """Registry 注册/查询/管理"""

    def setUp(self):
        self.reg = FetchRegistry()
        self.reg.register_fn(
            name="src_a", category="daily", fn=lambda c, days=120: f"data_a_{c}",
            priority=10,
        )
        self.reg.register_fn(
            name="src_b", category="daily", fn=lambda c, days=120: f"data_b_{c}",
            priority=20,
        )
        self.reg.register_fn(
            name="src_c", category="realtime", fn=lambda c: f"rt_{c}",
            priority=5,
        )

    def test_register_and_get(self):
        self.assertEqual(self.reg.get("src_a").priority, 10)
        self.assertIsNone(self.reg.get("nonexistent"))

    def test_list_by_category_sorted_by_priority(self):
        dailies = self.reg.list_by_category("daily")
        self.assertEqual([s.name for s in dailies], ["src_a", "src_b"])
        reals = self.reg.list_by_category("realtime")
        self.assertEqual([s.name for s in reals], ["src_c"])

    def test_categories(self):
        cats = sorted(self.reg.categories())
        self.assertEqual(cats, ["daily", "realtime"])

    def test_enable_disable(self):
        self.reg.disable("src_a")
        self.assertFalse(self.reg.get("src_a").enabled)
        dailies = self.reg.list_by_category("daily")
        self.assertEqual([s.name for s in dailies], ["src_b"])  # a 被过滤
        self.reg.enable("src_a")
        dailies = self.reg.list_by_category("daily")
        self.assertEqual(len(dailies), 2)

    def test_stats(self):
        stats = self.reg.stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["enabled"], 3)
        self.assertEqual(stats["disabled"], 0)
        self.assertEqual(stats["by_category"], {"daily": 2, "realtime": 1})

    def test_double_register_warns_overwrites(self):
        """重复注册覆盖 (不抛错)"""
        self.reg.register_fn(name="src_a", category="daily", fn=lambda c, days=120: "v2_data")
        self.assertEqual(len(self.reg.list_all()), 3)  # 数量不变
        # 验证 fn 被新版本替换
        new_src = self.reg.get("src_a")
        self.assertEqual(new_src.fn("000001", days=30), "v2_data")


def fetch_with_registry_on(reg, category, code, **kw):
    """小工具: 在指定 registry 上 fetch (测试用)"""
    # 直接调内部的 _try_one 因为 fetch_with_registry 用全局 registry
    from tuixue_v3.data_source_registry import _try_one
    sources = reg.list_by_category(category)
    if not sources:
        return FetchResult(data=None, source="", elapsed_ms=0, attempts=0)
    return FetchResult(
        data=_try_one(sources[0], code, 1.0, **kw),
        source=sources[0].name, elapsed_ms=0, attempts=1,
    )


class TestFetchWithRegistry(unittest.TestCase):
    """统一 fetch 入口行为"""

    def setUp(self):
        # 用独立 registry 测试,避免污染全局
        self.reg = FetchRegistry()

    def test_no_sources_returns_empty(self):
        result = fetch_with_registry("nonexistent_category_xyz", "600519")
        self.assertIsNone(result.data)
        self.assertEqual(result.attempts, 0)

    def test_race_top_n_returns_first_success(self):
        # 注册 3 个源: 第 2 个最快成功
        self.reg.register_fn(
            name="slow_ok", category="daily", fn=lambda c, days=120: f"slow_{c}",
            priority=10, timeout=2.0,
        )
        self.reg.register_fn(
            name="fast_ok", category="daily", fn=lambda c, days=120: f"fast_{c}",
            priority=20, timeout=2.0,
        )
        self.reg.register_fn(
            name="fallback", category="daily", fn=lambda c, days=120: f"fb_{c}",
            priority=30, timeout=2.0,
        )

        # monkey-patch global registry for this test
        import tuixue_v3.data_source_registry as mod
        orig_registry = mod.registry
        mod.registry = self.reg
        try:
            result = fetch_with_registry("daily", "600519")
            # 应拿到 Top 3 任意一个 (都成功),data 不为空
            self.assertIsNotNone(result.data)
            self.assertIn(result.source, ["slow_ok", "fast_ok", "fallback"])
            self.assertGreater(result.elapsed_ms, 0)
        finally:
            mod.registry = orig_registry

    def test_requires_validator_filters_invalid(self):
        """requires 返回 False 时,该源被当作失败"""
        self.reg.register_fn(
            name="bad", category="daily",
            fn=lambda c, days=120: "useless",
            priority=10, requires=lambda d: False,  # 永远拒绝
        )
        self.reg.register_fn(
            name="good", category="daily",
            fn=lambda c, days=120: "good_data",
            priority=20,
        )

        import tuixue_v3.data_source_registry as mod
        orig = mod.registry
        mod.registry = self.reg
        try:
            result = fetch_with_registry("daily", "600519")
            self.assertEqual(result.data, "good_data")
            self.assertEqual(result.source, "good")
            # fallback_chain 应记录两个尝试
            self.assertGreaterEqual(len(result.fallback_chain), 1)
        finally:
            mod.registry = orig

    def test_disabled_source_skipped(self):
        self.reg.register_fn(
            name="dis", category="daily",
            fn=lambda c, days=120: "disabled",
            priority=10,
        )
        self.reg.register_fn(
            name="en", category="daily",
            fn=lambda c, days=120: "enabled",
            priority=20,
        )
        self.reg.disable("dis")

        import tuixue_v3.data_source_registry as mod
        orig = mod.registry
        mod.registry = self.reg
        try:
            result = fetch_with_registry("daily", "600519")
            self.assertEqual(result.source, "en")
        finally:
            mod.registry = orig

    def test_all_sources_fail_returns_none(self):
        self.reg.register_fn(
            name="always_fail", category="daily",
            fn=lambda c, days=120: None,  # 永远返 None
            priority=10,
        )
        import tuixue_v3.data_source_registry as mod
        orig = mod.registry
        mod.registry = self.reg
        try:
            result = fetch_with_registry("daily", "600519")
            self.assertIsNone(result.data)
            self.assertEqual(result.source, "")
            self.assertEqual(result.attempts, 1)
        finally:
            mod.registry = orig


class TestDecorator(unittest.TestCase):
    """register_source 装饰器"""

    def test_decorator_registers_function(self):
        @register_source(
            name="test_dec", category="daily", priority=99,
            display_name="Test Source",
        )
        def my_fetch(code, days=120):
            return f"dec_{code}"

        src = registry.get("test_dec")
        self.assertIsNotNone(src)
        self.assertEqual(src.category, "daily")
        self.assertEqual(src.priority, 99)
        self.assertEqual(src.display_name, "Test Source")
        self.assertEqual(src.fn("600519", days=30), "dec_600519")


class TestLegacyBootstrap(unittest.TestCase):
    """legacy 源 bootstrap 不挂"""

    def test_bootstrap_idempotent(self):
        """重复 bootstrap 不抛错,且不重复注册"""
        try:
            from tuixue_v3.data_source_registry import bootstrap_from_legacy
            before = len(registry.list_all())
            bootstrap_from_legacy()
            after1 = len(registry.list_all())
            bootstrap_from_legacy()  # 第二次
            after2 = len(registry.list_all())
            # 第二次不能增加源数量 (因为已有同 name 不重复)
            self.assertEqual(after1, after2)
            self.assertGreaterEqual(after1, before)
        except ImportError:
            self.skipTest("lib_common 不可用,跳过 legacy bootstrap 测试")


if __name__ == "__main__":
    unittest.main(verbosity=2)
