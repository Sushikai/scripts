"""LLM 适配器抽象基类。"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator


@dataclass
class LLMMessage:
    role: str  # system | user | assistant
    content: str


@dataclass
class LLMResponse:
    content: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    finish_reason: str = "stop"
    raw: dict = field(default_factory=dict)


class BaseLLM(ABC):
    """所有 LLM 适配器的统一接口。"""

    name: str = "base"
    default_model: str = ""

    def __init__(self, api_key: str, base_url: str = "", model: str = ""):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model or self.default_model

    @abstractmethod
    async def chat(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        """非流式对话。"""

    @abstractmethod
    async def stream(self, messages: list[LLMMessage], **kwargs) -> AsyncIterator[str]:
        """SSE 流式对话。yield content chunk。"""

    async def test(self, prompt: str = "你好,请回复 OK") -> LLMResponse:
        """连接性测试。"""
        return await self.chat([
            LLMMessage(role="user", content=prompt),
        ], max_tokens=20)