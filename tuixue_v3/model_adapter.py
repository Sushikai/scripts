#!/usr/bin/env python3
"""
tuixue_v3/model_adapter.py
Ship 4/100 — ModelAdapter 多模型共存 (MiniMax 主 + DeepSeek 辅 + Qwen 本地兜底)

设计目标:
1) 适配器模式: 不同 AI provider 走不同 adapter, 统一 CallSpec 输出
2) 主备链路: MiniMax (主) → DeepSeek (辅) → Qwen 本地 (兜底) → 失败抛错
3) 配置驱动: env / _constants 切换默认模型, 运行时也可动态切
4) 接入成本: 新 provider 只需 1 个 ModelAdapter 子类 + register()
5) 不破坏现有 ai_client.call(): 复用 CallSpec/熔断/metrics

已实现 adapter:
- MiniMaxAdapter (默认主): api.minimaxi.com/v1/text/chatcompletion_v2
- DeepSeekAdapter (辅助): api.deepseek.com/v1/chat/completions
- QwenLocalAdapter (兜底): localhost:11434/v1/chat/completions (Ollama)

2026-08-02 Ship 4 — 10000 轮迭代 P0 第四步
"""
from __future__ import annotations

import logging
import os
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AdapterSpec:
    """统一 adapter 输出 — 可直接喂 ai_client.call()"""
    url: str
    headers: dict
    body: dict
    model: str
    name: str                          # metric tag
    timeout: float = 35.0


class ModelAdapter(ABC):
    """所有 model adapter 的基类"""

    name: str = "base"
    provider: str = "unknown"

    @abstractmethod
    def is_available(self) -> bool:
        """provider 是否可用 (key 存在 / 服务在线)"""
        ...

    @abstractmethod
    def build_spec(self, *, system: str, user: str, model: Optional[str] = None,
                   temperature: float = 0.7, max_tokens: int = 3500,
                   name: str = "main") -> AdapterSpec:
        """构造 CallSpec 喂给 ai_client.call()"""
        ...

    def __repr__(self):
        return f"<{self.__class__.__name__} provider={self.provider}>"


# ═══════════════════════════════════════════════════════
# MiniMax Adapter (默认主)
# ═══════════════════════════════════════════════════════

class MiniMaxAdapter(ModelAdapter):
    name = "minimax"
    provider = "minimax"

    DEFAULT_MODEL = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")
    DEFAULT_URL = "https://api.minimaxi.com/v1/text/chatcompletion_v2"

    def __init__(self, api_key: Optional[str] = None, url: Optional[str] = None,
                 model: Optional[str] = None):
        self.api_key = api_key or os.environ.get("MINIMAX_API_KEY", "")
        self.url = url or self.DEFAULT_URL
        self.model = model or self.DEFAULT_MODEL

    def is_available(self) -> bool:
        return bool(self.api_key)

    def build_spec(self, *, system, user, model=None, temperature=0.7,
                   max_tokens=3500, name="main") -> AdapterSpec:
        return AdapterSpec(
            url=self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            body={
                "model": model or self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            model=model or self.model,
            name=f"{self.name}_{name}",
            timeout=35.0,
        )


# ═══════════════════════════════════════════════════════
# DeepSeek Adapter (辅助 — 用于情绪打分等大批量廉价任务)
# ═══════════════════════════════════════════════════════

class DeepSeekAdapter(ModelAdapter):
    name = "deepseek"
    provider = "deepseek"

    DEFAULT_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    DEFAULT_URL = "https://api.deepseek.com/v1/chat/completions"

    def __init__(self, api_key: Optional[str] = None, url: Optional[str] = None,
                 model: Optional[str] = None):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.url = url or self.DEFAULT_URL
        self.model = model or self.DEFAULT_MODEL

    def is_available(self) -> bool:
        return bool(self.api_key)

    def build_spec(self, *, system, user, model=None, temperature=0.7,
                   max_tokens=3500, name="main") -> AdapterSpec:
        return AdapterSpec(
            url=self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            body={
                "model": model or self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            },
            model=model or self.model,
            name=f"{self.name}_{name}",
            timeout=30.0,
        )


# ═══════════════════════════════════════════════════════
# Qwen Local Adapter (兜底 — Ollama 本地推理, 零边际成本)
# ═══════════════════════════════════════════════════════

class QwenLocalAdapter(ModelAdapter):
    name = "qwen_local"
    provider = "ollama"

    DEFAULT_MODEL = os.environ.get("QWEN_LOCAL_MODEL", "qwen2.5:7b")
    DEFAULT_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/v1/chat/completions")

    def __init__(self, url: Optional[str] = None, model: Optional[str] = None):
        self.url = url or self.DEFAULT_URL
        self.model = model or self.DEFAULT_MODEL

    def is_available(self) -> bool:
        """Ollama 本地服务 — 默认乐观假设可用

        注: 不做 ping, 避免启动时阻塞。真实调用失败时 ai_client 自动切主备。
        如需严格探测, 显式调用 health_check()
        """
        return True

    def health_check(self) -> bool:
        """主动 ping (耗时, 仅供运维排查)"""
        import urllib.request
        try:
            base = self.url.replace("/v1/chat/completions", "")
            req = urllib.request.Request(f"{base}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=1.0) as r:
                return r.status == 200
        except Exception:
            return False

    def build_spec(self, *, system, user, model=None, temperature=0.7,
                   max_tokens=3500, name="main") -> AdapterSpec:
        return AdapterSpec(
            url=self.url,
            headers={"Content-Type": "application/json"},
            body={
                "model": model or self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            model=model or self.model,
            name=f"{self.name}_{name}",
            timeout=60.0,  # 本地推理慢
        )


# ═══════════════════════════════════════════════════════
# Adapter Registry + 主备链路
# ═══════════════════════════════════════════════════════

class ModelAdapterRegistry:
    """全局 adapter 注册表 — 默认主备链路"""

    def __init__(self):
        self._adapters: dict[str, ModelAdapter] = {}
        self._lock = threading.RLock()
        self._primary: Optional[str] = None  # 默认主 adapter name

    def register(self, adapter: ModelAdapter, primary: bool = False):
        with self._lock:
            self._adapters[adapter.name] = adapter
            if primary or self._primary is None:
                self._primary = adapter.name
            logger.info(f"注册 ModelAdapter: {adapter.name} ({adapter.provider})"
                        f"{' [主]' if primary else ''}")

    def get(self, name: str) -> Optional[ModelAdapter]:
        with self._lock:
            return self._adapters.get(name)

    def primary(self) -> ModelAdapter:
        with self._lock:
            if self._primary and self._primary in self._adapters:
                return self._adapters[self._primary]
            # 兜底: 第一个可用的
            for a in self._adapters.values():
                return a
            raise RuntimeError("无 ModelAdapter 注册")

    def fallback_chain(self) -> list[ModelAdapter]:
        """主 → 辅 → 兜底, 按可用性过滤"""
        with self._lock:
            chain = []
            # 主在最前
            if self._primary and self._primary in self._adapters:
                chain.append(self._adapters[self._primary])
            # 其余按 register 顺序
            for name, adapter in self._adapters.items():
                if adapter.name != self._primary:
                    chain.append(adapter)
            # 只返回可用的
            return [a for a in chain if a.is_available()]

    def list_all(self) -> list[str]:
        with self._lock:
            return list(self._adapters.keys())

    def set_primary(self, name: str):
        with self._lock:
            if name in self._adapters:
                self._primary = name
                logger.info(f"切换主 adapter: {name}")


# 全局单例
adapter_registry = ModelAdapterRegistry()


def bootstrap_default_chain():
    """注册默认主备链路: MiniMax 主 → DeepSeek 辅 → Qwen 本地兜底"""
    if adapter_registry.list_all():
        return  # 已注册 (测试场景)

    minimax = MiniMaxAdapter()
    deepseek = DeepSeekAdapter()
    qwen = QwenLocalAdapter()

    # MiniMax 主 (只要有 key)
    if minimax.is_available():
        adapter_registry.register(minimax, primary=True)
    else:
        logger.info("MiniMax API key 未配置,降级到 DeepSeek")

    # DeepSeek 辅 (常驻)
    if deepseek.is_available():
        adapter_registry.register(deepseek, primary=(not minimax.is_available()))

    # Qwen 本地兜底 (常驻)
    adapter_registry.register(qwen)

    logger.info(
        f"Adapter chain: primary={adapter_registry._primary}, "
        f"available={[a.name for a in adapter_registry.fallback_chain()]}"
    )


def call_with_fallback(*, system: str, user: str, **kwargs) -> AdapterSpec:
    """主备链路取首个可用 adapter 的 spec

    Args:
        system: system prompt
        user: user prompt
        **kwargs: 透传给 adapter.build_spec (model/temperature/max_tokens/name)

    Returns:
        AdapterSpec: 可直接喂给 ai_client.call()

    Raises:
        RuntimeError: 所有 adapter 不可用
    """
    chain = adapter_registry.fallback_chain()
    if not chain:
        raise RuntimeError("所有 ModelAdapter 都不可用 (检查 API key / Ollama 状态)")
    adapter = chain[0]
    return adapter.build_spec(system=system, user=user, **kwargs)


def call_with_chain(*, system: str, user: str, **kwargs) -> list[AdapterSpec]:
    """返回主备链路所有可用 adapter 的 spec (供业务层循环重试)"""
    chain = adapter_registry.fallback_chain()
    if not chain:
        raise RuntimeError("所有 ModelAdapter 都不可用")
    return [a.build_spec(system=system, user=user, **kwargs) for a in chain]


# 模块导入时自动 bootstrap (容错:无 key 时只兜底)
try:
    bootstrap_default_chain()
except Exception as e:
    logger.debug(f"bootstrap_default_chain 失败 (非阻塞): {e}")
