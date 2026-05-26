#!/usr/bin/env python3
"""
AI 处理器 - Ollama 本地模型进行内容清洗、总结、分类、向量存储
支持：qwen2.5:32b / gemma3:4b
"""

from __future__ import annotations

import json
import logging
import time
import hashlib
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# ============ Ollama 客户端 ============

class OllamaClient:
    """Ollama API 封装（线程安全，连接池化）"""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "qwen2.5:32b-instruct-q4_K_M", timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=1,
        )
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """调用 /api/generate"""
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if system:
            payload["system"] = system

        for attempt in range(3):
            try:
                resp = self._session.post(url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json().get("response", "").strip()
            except requests.exceptions.Timeout:
                logger.warning(f"Ollama 超时，重试 {attempt + 1}/3")
                time.sleep(3)
            except Exception as e:
                logger.error(f"Ollama 请求失败: {e}")
                return ""

        return ""

    def chat(self, messages: list[dict], model: Optional[str] = None) -> str:
        """调用 /api/chat（多轮对话）"""
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
        }
        resp = self._session.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "").strip()


# ============ 内容处理器 ============

class MaterialProcessor:
    """
    素材 AI 处理器
    功能：
    1. 内容清洗（去广告、去水印）
    2. 分类打标（火花宝宝 / 不存在的小镇）
    3. 总结改写
    4. 向量存储（Chroma）
    """

    SYSTEM_PROMPT = """你是一个专业的短视频素材分析专家。
对输入的文字内容进行分析，提取可用于视频创作的素材。
输出严格 JSON 格式，不要有多余文字。
JSON 字段：
- clean_text: 清洗后的文字（去除广告、水印、无关内容）
- category: 分类（"火花宝宝" / "不存在的小镇" / "通用"）
- mood: 内容情绪（温暖/治愈/可爱/荒诞/奇幻/幽默/感人等）
- tags: 关键词标签列表（3-8个）
- usable: 是否可用（true/false）及原因
- suggestion: 使用建议（如何用于视频创作）"""

    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        model: str = "gemma3:4b",           # gemma3:4b 更快，用于日常处理
        slow_model: str = "qwen2.5:32b-instruct-q4_K_M",  # 大模型用于精细分析
        chroma_path: str = "database/chroma_db",
        collection_name: str = "video_materials",
        use_fast_model: bool = True,
    ):
        self.ollama = OllamaClient(base_url=ollama_url, model=model)
        self.slow_model = slow_model
        self.use_fast_model = use_fast_model
        self.chroma_path = Path(chroma_path)
        self.collection_name = collection_name
        self._chroma_client = None
        self._collection = None

    # ---- Ollama 调用 ----

    def analyze(self, text: str, retry_model: bool = True) -> dict:
        """
        分析单条素材
        Returns:
            {"clean_text": "", "category": "", "mood": "", "tags": [], "usable": bool, "reason": "", "suggestion": ""}
        """
        result = self._call_ollama(text, retry_model=retry_model, fast=self.use_fast_model)
        if not result:
            return {"error": "分析失败", "usable": False, "reason": "AI响应为空"}

        try:
            # 尝试解析 JSON
            # 去掉可能的 markdown 代码块
            if result.startswith("```"):
                lines = result.split("\n")
                result = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

            parsed = json.loads(result)
            return self._validate_result(parsed, text)
        except json.JSONDecodeError:
            logger.warning(f"JSON 解析失败，尝试文本解析: {result[:100]}")
            return self._fallback_parse(text, result)

    def _call_ollama(self, text: str, retry_model: bool = True, fast: bool = True) -> str:
        """调用 Ollama 并处理错误"""
        # 快速模式：用短 prompt + 少 token
        if fast:
            short_prompt = f"""分析素材，返回JSON格式：
{{"clean_text":"清洗后文字","category":"分类(火花宝宝/不存在的小镇/通用)","mood":"情绪","tags":["标签1","标签2"],"usable":true/false}}
素材：{text[:300]}"""
            short_system = "你是一个素材分析助手，直接返回JSON，不要多余文字。"
            try:
                result = self.ollama.generate(
                    prompt=short_prompt,
                    system=short_system,
                    temperature=0.3,
                    max_tokens=300,
                )
                if result:
                    return result
            except Exception as e:
                logger.warning(f"快速模式失败: {e}")

        # 标准模式
        try:
            result = self.ollama.generate(
                prompt=f"分析以下短视频素材：\n\n{text}",
                system=self.SYSTEM_PROMPT,
                temperature=0.3,
                max_tokens=1024,
            )
            if result:
                return result
        except Exception as e:
            logger.error(f"Ollama 调用失败: {e}")

        # 备用大模型
        if retry_model and self.slow_model:
            try:
                old_model = self.ollama.model
                self.ollama.model = self.slow_model
                result = self.ollama.generate(
                    prompt=f"分析素材（JSON格式）：\n{text[:500]}",
                    temperature=0.3,
                    max_tokens=512,
                )
                self.ollama.model = old_model
                return result
            except Exception as e:
                logger.error(f"备用模型也失败: {e}")
                self.ollama.model = old_model
                return ""

        return ""

    def _validate_result(self, parsed: dict, original: str) -> dict:
        """验证并补充分析结果"""
        result = {
            "clean_text": parsed.get("clean_text", original[:200]),
            "category": parsed.get("category", "通用"),
            "mood": parsed.get("mood", "未知"),
            "tags": parsed.get("tags", []),
            "usable": bool(parsed.get("usable", True)),
            "reason": parsed.get("reason", ""),
            "suggestion": parsed.get("suggestion", ""),
        }
        # 简单过滤
        if len(result["clean_text"]) < 5:
            result["usable"] = False
            result["reason"] = "文字过短"
        return result

    def _fallback_parse(self, text: str, raw: str) -> dict:
        """无法解析 JSON 时的降级处理"""
        # 尝试关键词匹配分类
        spark_keywords = ["宝宝", "萌娃", "可爱", "小孩", "儿童", "宝贝", "亲子", "娃", "童"]
        town_keywords = ["探险", "荒诞", "奇幻", "魔法", "怪物", "神秘", "奇怪", "梦境", "小镇"]

        category = "通用"
        if any(kw in text for kw in spark_keywords):
            category = "火花宝宝"
        elif any(kw in text for kw in town_keywords):
            category = "不存在的小镇"

        return {
            "clean_text": text[:200],
            "category": category,
            "mood": "未知",
            "tags": [],
            "usable": True,
            "reason": "自动分类",
            "suggestion": "根据关键词自动分类，建议人工审核",
            "raw_llm_response": raw[:200],
        }

    # ---- 批量处理（多线程）----

    def process_batch(self, items: list[dict], batch_size: int = 10, workers: int = 4) -> list[dict]:
        """
        批量处理素材（多线程并发）
        items: [{"id": "...", "platform": "...", "raw_text": "...", ...}]
        workers: 并发线程数，默认4
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = [None] * len(items)
        total = len(items)

        def process_one(idx_item):
            idx, item = idx_item
            analyzed = self.analyze(item.get("raw_text", ""))
            analyzed["id"] = item.get("id", "")
            analyzed["platform"] = item.get("platform", "")
            analyzed["keyword"] = item.get("keyword", "")
            return idx, analyzed

        logger.info(f"开始并发处理: {total} 条，{workers} 线程")
        done = 0

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(process_one, (i, item)): i
                for i, item in enumerate(items)
            }
            for future in as_completed(futures):
                idx, analyzed = future.result()
                results[idx] = analyzed
                done += 1
                if done % 5 == 0 or done == total:
                    logger.info(f"处理进度: {done}/{total}")

        return [r for r in results if r is not None]

    # ---- Chroma 向量存储 ----

    def init_vector_store(self):
        """初始化 Chroma 向量库"""
        try:
            import chromadb
            from chromadb.config import Settings

            self._chroma_client = chromadb.PersistentClient(
                path=str(self.chroma_path)
            )
            self._collection = self._chroma_client.get_or_create_collection(
                name=self.collection_name,
                metadata={"description": "短视频素材向量库"},
            )
            logger.info(f"Chroma 向量库初始化成功: {self.chroma_path}")
            return True
        except ImportError:
            logger.error("请安装 chromadb: pip install chromadb")
            return False
        except Exception as e:
            logger.error(f"Chroma 初始化失败: {e}")
            return False

    def add_to_vector_store(
        self,
        texts: list[str],
        ids: list[str],
        metadatas: Optional[list[dict]] = None,
    ):
        """添加向量到存储"""
        if self._collection is None:
            self.init_vector_store()

        if not self._collection:
            logger.warning("Chroma 未初始化，跳过向量化")
            return

        try:
            self._collection.add(
                documents=texts,
                ids=ids,
                metadatas=metadatas or [{}] * len(texts),
            )
            logger.info(f"已添加 {len(texts)} 条向量")
        except Exception as e:
            logger.error(f"添加向量失败: {e}")

    def search_similar(
        self,
        query: str,
        n_results: int = 5,
        where: Optional[dict] = None,
    ) -> list[dict]:
        """相似搜索"""
        if self._collection is None:
            self.init_vector_store()

        if not self._collection:
            return []

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where,
            )
            return [
                {
                    "id": results["ids"][0][i],
                    "text": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": results["distances"][0][i],
                }
                for i in range(len(results["ids"][0]))
            ]
        except Exception as e:
            logger.error(f"向量搜索失败: {e}")
            return []

    # ---- 辅助功能 ----

    def deduplicate(self, items: list[dict], hash_field: str = "hash") -> list[dict]:
        """
        内容去重
        基于文本 hash 去除重复条目
        """
        seen = set()
        unique = []
        for item in items:
            h = item.get(hash_field, "")
            if not h:
                h = hashlib.md5(item.get("raw_text", "").encode()).hexdigest()
            if h not in seen:
                seen.add(h)
                unique.append(item)
        return unique

    def filter_low_quality(self, items: list[dict], min_length: int = 10) -> list[dict]:
        """过滤低质量内容"""
        return [
            item
            for item in items
            if len(item.get("clean_text", item.get("raw_text", ""))) >= min_length
        ]

    def export_to_json(
        self,
        items: list[dict],
        output_path: str | Path,
        category: Optional[str] = None,
    ):
        """导出为 JSON 文件"""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        filtered = items if not category else [i for i in items if i.get("category") == category]

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(filtered, f, ensure_ascii=False, indent=2)

        logger.info(f"已导出 {len(filtered)} 条到 {output_path}")


# ---- 快速函数 ----

_processor_instance = None

def process_material(text: str) -> dict:
    """一行函数：快速处理单条素材"""
    global _processor_instance
    if _processor_instance is None:
        _processor_instance = MaterialProcessor()
    return _processor_instance.analyze(text)


# ---- 单元测试 ----
if __name__ == "__main__":
    print("AI 素材处理器模块已加载")
    print("测试 Ollama 连接...")

    client = OllamaClient()
    test_text = "宝宝吃饭好可爱，笑起来萌萌的"

    try:
        result = client.generate(
            prompt=f"简洁分析：{test_text}",
            system="你是一个素材分析助手，用中文回答",
            max_tokens=200,
        )
        print(f"✅ Ollama 连接成功")
        print(f"测试结果: {result[:100]}...")
    except Exception as e:
        print(f"⚠️ Ollama 未连接: {e}")
        print("请确保 Ollama 已启动: ollama serve")