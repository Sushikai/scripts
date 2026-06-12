"""MiniMax API Client — 使用 Anthropic SDK"""
import os, re, requests
from .llm_config import MINIMAX_CONFIG

class MiniMaxClient:
    def __init__(self, config: dict = None):
        self.cfg = config or MINIMAX_CONFIG
        self.base_url = self.cfg["baseUrl"]
        self.api_key = self.cfg["apiKey"]
        self.model = self.cfg["model"]
        self.timeout = self.cfg.get("timeout", 60)
        self.retry = self.cfg.get("retry_times", 3)

    def _client(self):
        """创建 Anthropic SDK 客户端"""
        import anthropic
        return anthropic.Anthropic(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout,
        )

    def _extract_text(self, msg) -> str:
        """从 Anthropic 响应中提取文本，兼容所有 block 类型"""
        for block in msg.content:
            # TextBlock / 纯文本
            if hasattr(block, 'text') and block.text:
                return block.text
            # MiniMax thinking block
            if hasattr(block, 'thinking') and block.thinking:
                return block.thinking
            # content 字段
            if hasattr(block, 'content') and block.content:
                return block.content
        return ""

    def score_topic(self, topic: str) -> float:
        """给话题打热度分（0-100），用于排序"""
        prompt = f"""你是一个新闻热度分析助手。
请评估话题「{topic}」的热度分数（0-100），考虑：
- 争议性/冲突程度
- 时效性
- 公众关注度
- 是否有重大影响

直接输出一个0-100的数字，不要解释。
分数："""
        try:
            client = self._client()
            msg = client.messages.create(
                model=self.model,
                max_tokens=10,
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}],
            )
            result = self._extract_text(msg).strip()
            score = float(result)
            return max(0.0, min(100.0, score))
        except Exception:
            return 50.0

    def generate_script(self, topic: str) -> str:
        """生成口播文案，直接自然，无AI反射"""
        # 动态导入config，避免循环引用
        try:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from info_gap_pipeline.config import SCRIPT_MAX_TOKENS, SCRIPT_TEMPERATURE
            max_tokens = SCRIPT_MAX_TOKENS
            temperature = SCRIPT_TEMPERATURE
        except Exception:
            max_tokens = 800
            temperature = 1.0

        system_prompt = (
            "你是一个接地气的B站口播博主，说话像朋友聊天，句子短，不啰嗦，直接开始说内容。"
            "风格：信息差揭秘系列，悬念开头，数据驱动，有深度有节奏。"
        )

        # 强化版prompt，引导生成更丰富的内容
        user_prompt = (
            f"话题：{topic}\n\n"
            "你是一个信息差视频博主，录制一期节目。\n"
            "【要求】\n"
            "1. 开头：「第一、{topic}」，直接陈述，不说大家好，直接进入。\n"
            "2. 信息密度要高，数据要说精确的（610万、48%、2.9%）。\n"
            "3. 允许长句，信息密度要高，不限制句子长度。\n"
            "4. 段内过渡词：然而、對此、不過、對此，專家強調。\n"
            "5. 结尾：「今日分享到此結束，感謝觀看」。\n"
            "6. 总字数：300-600字（4-6分钟口播量）。\n\n"
            "现在直接输出文案，直接开始："
        ).format(topic=topic)

        for attempt in range(self.retry):
            try:
                client = self._client()
                msg = client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                text = self._extract_text(msg).strip()
                # 清理空行和空格
                text = re.sub(r'\s+', '', text)
                # 过滤 prompt reflection（中英文所有变体）
                text_lower = text.lower()
                reflection_prefixes = [
                    "the user", "the assistant", "user asks", "you are a",
                    "好的，我", "我需要", "主题是", "用户要求", "用户想要", "用户想",
                    "用户需要我", "用户让我", "用户要求我", "用户需要我",
                    "我需要你", "我应该", "我来帮你",
                    "这个问题", "这个要求", "这个任务", "这个请求",
                    "我理解", "根据您", "按照您的",
                ]
                if any(text_lower.startswith(p.lower()) for p in reflection_prefixes):
                    continue
                # 过滤包含要求/任务描述的长反射文本
                if len(text) > 200 and any(kw in text for kw in ["要求", "需要我", "要求我", "任务"]):
                    continue
                # 过滤 markdown 格式
                if text.startswith("#") or text.startswith("-") or text.startswith("*"):
                    continue
                # 合理长度：100-600字（4-6分钟视频需要300-600字口播）
                if 100 <= len(text) <= 600:
                    return text
            except Exception:
                pass
        return ""


def generate_script_ollama(topic: str, model: str = "qwen2.5:32b-instruct-q4_K_M") -> str:
    """
    完全基于真实参考视频BV1EY7k6aEPg的逐字 transcript 重写。
    真实特征：信息密度高、长句为主、新闻播报感、「第一、」开头、过渡词自然。
    """
    prompt = f"""你是一个信息差视频博主，录制一期节目。

【话题】{topic}

【真实节目标注文风】（严格照做，一字不差）

1. 开头格式：「第一、{topic}」，直接陈述，不说"大家好"，不解释，直接进入。

2. 句子长度不限制。允许长句，信息密度要高。禁止把句子拆成短句。每句能说多长就说多长。

3. 数字说精确的：610万、48%、2.9%，不需要说"几百""几万"。

4. 段内过渡词：
   「然而」——引出争议或反面观点
   「對此」——引出官方/专家回应
   「不過」——引出转折或补充
   「專家強調」——引出解决方案

5. 如果内容较多，使用「第二、」「第三、」等继续展开（不是第2、第3，是中文数字）

6. 禁止出现：
   - "第1、""第2、""第X、"
   - "首先其次最后"
   - "据悉""数据显示"
   - "相信大家""让我们一起"
   - "真的吗""这就离谱""你想想"

7. 每段结构：事件名 → 具体内容 → 过渡词 → 争议/影响/数据 → 下一段

8. 结尾必须用：「今日分享到此結束，感謝觀看」

9. 语速要快，中等偏快，约5-6字/秒。

10. 总字数：300-600字（4-6分钟口播量）。

现在直接输出文案，直接开始："""
    try:
        resp = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "num_predict": 800,
                    "temperature": 0.8,
                    "num_ctx": 8192,
                    "stop": ["\n\n\n", "---", "===", "【", "##", "提示词", "以下是"],
                }
            },
            timeout=300,
        )
        if resp.status_code == 200:
            result = resp.json().get("response", "").strip()
            result = re.sub(r'思考.*', '', result)
            result = re.sub(r'任務.*', '', result)
            result = re.sub(r'【.*', '', result)
            result = re.sub(r'^\*.*', '', result)
            result = re.sub(r'^#.*', '', result)
            result = re.sub(r'^\d+\).*', '', result)
            result = re.sub(r'```+[\s\S]*?```+', '', result)
            # 匹配真实格式：第一、第二、第三
            idx = re.search(r'第[一二三四五六七八九十]+、', result)
            if idx:
                result = result[idx.start():]
            result = result.strip()
            if not result or len(result) < 40:
                raise ValueError(f"生成的文案过短或为空: {result[:50]}")
            if 40 <= len(result) <= 800:
                return result
    except Exception:
        pass
    return ""

