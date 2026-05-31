"""
llm_utils - LLM 统一接口
"""
from llm_utils.client import call_ollama, call_minimax, generateReply

__all__ = ['call_ollama', 'call_minimax', 'generateReply']