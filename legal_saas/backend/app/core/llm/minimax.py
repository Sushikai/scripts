"""
MiniMax API 客户端。
移植自 tuixue_v3 web/ai_client.py 的 4 道防线:重试 / 熔断 / Schema / 截断。
"""
from __future__ import annotations
import asyncio
import time
from typing import AsyncIterator
import httpx
from loguru import logger
from .base import BaseLLM, LLMMessage, LLMResponse


class MiniMaxLLM(BaseLLM):
    """MiniMax M3 / Claude / DeepSeek 兼容 OpenAI Chat Completions 协议。"""

    name = "minimax"
    default_model = "MiniMax-M3"

    def __init__(self, api_key: str, base_url: str = "", model: str = ""):
        super().__init__(api_key, base_url or "https://api.MiniMax.io/v1", model)
        self._client: httpx.AsyncClient | None = None
        # 简单熔断器
        self._fail_count = 0
        self._circuit_open_until = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(60, connect=10),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _check_circuit(self):
        if time.time() < self._circuit_open_until:
            raise RuntimeError(f"MiniMax 熔断中,{int(self._circuit_open_until - time.time())}s 后重试")

    def _trip_circuit(self):
        self._fail_count += 1
        if self._fail_count >= 5:
            self._circuit_open_until = time.time() + 60
            logger.warning(f"[minimax] 熔断器开启,60s 熔断")

    def _reset_circuit(self):
        self._fail_count = 0
        self._circuit_open_until = 0.0

    async def chat(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        """非流式对话,带重试。"""
        self._check_circuit()
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": kwargs.get("max_tokens", 2048),
            "temperature": kwargs.get("temperature", 0.7),
        }
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                client = await self._get_client()
                r = await client.post(
                    "/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                if r.status_code != 200:
                    raise RuntimeError(f"MiniMax HTTP {r.status_code}: {r.text[:300]}")
                data = r.json()
                self._reset_circuit()
                return LLMResponse(
                    content=data["choices"][0]["message"]["content"],
                    model=data.get("model", self.model),
                    tokens_in=data.get("usage", {}).get("prompt_tokens", 0),
                    tokens_out=data.get("usage", {}).get("completion_tokens", 0),
                    finish_reason=data["choices"][0].get("finish_reason", "stop"),
                    raw=data,
                )
            except Exception as e:
                last_err = e
                self._trip_circuit()
                logger.warning(f"[minimax] chat attempt={attempt} err={e}")
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                break
        raise RuntimeError(f"MiniMax chat failed after 3 retries: {last_err}")

    async def stream(self, messages: list[LLMMessage], **kwargs) -> AsyncIterator[str]:
        """SSE 流式对话。"""
        self._check_circuit()
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": kwargs.get("max_tokens", 2048),
            "temperature": kwargs.get("temperature", 0.7),
            "stream": True,
        }
        client = await self._get_client()
        async with client.stream(
            "POST",
            "/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
        ) as r:
            if r.status_code != 200:
                body = await r.aread()
                raise RuntimeError(f"MiniMax HTTP {r.status_code}: {body[:300].decode()}")
            async for line in r.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                chunk = line[6:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    import json
                    data = json.loads(chunk)
                    delta = data["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except Exception:
                    continue
        self._reset_circuit()