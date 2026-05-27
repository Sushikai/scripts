"""MiniMax API Client — 使用 Anthropic SDK"""
import os
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
        """从 Anthropic 响应中提取文本（跳过 ThinkingBlock）"""
        for block in msg.content:
            if hasattr(block, 'text'):
                return block.text
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
        """生成200-250字新闻播报文案，口语化像真人，匹配约30-40秒口播"""
        prompt = f"""你是一个B站信息差视频的文案写作助手。

请围绕话题「{topic}」写一段150-180字的口播文案。

要求（极其重要）：
1. 像真人在镜头前说话，不要像记者播报，更不要像AI写稿
2. 用口语词汇，避免书面语（不要用"据悉"、"数据显示"、"根据"这种开头）
3. 可以用"哎"、"诶"、"你知道吗"这种口语词增加真实感，但不要太刻意
4. 句子要短，每句不超过15个字，节奏快
5. 观点明确、语气轻松，像跟朋友聊天一样讲新闻
6. 结尾自然收束，不要"以上就是今天的全部内容"
7. 不要加任何引导评论、点赞、关注的话
8. 直接输出文案，不要前缀不要注释

文案："""
        for attempt in range(self.retry):
            try:
                client = self._client()
                msg = client.messages.create(
                    model=self.model,
                    max_tokens=400,
                    temperature=0.8,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = self._extract_text(msg).strip()
                # 如果返回文本太短（说明只有ThinkingBlock没有TextBlock），重试
                # 要求至少100字以上（约15秒音频@+30%语速），否则重试
                if len(text) >= 100:
                    return text
            except Exception:
                pass
        return ""