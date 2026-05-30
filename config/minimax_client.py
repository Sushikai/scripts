"""MiniMax API Client — 使用 Anthropic SDK"""
import os, re
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
        """生成120-150字新闻播报文案，诙谐幽默像真人，匹配约15秒口播"""
        prompt = f"""你是一个有10年经验的B站口播博主，专做热点新闻信息差，风格诙谐幽默。

请围绕话题「{topic}」写一段120-150字的口播文案。

要求（极其重要）：
1. 开头必须有强钩子！用意外、震惊、反差的方式切入，让人想点进来听
2. 像单口喜剧演员讲新闻，用幽默方式包装信息，不是念稿不是播报
3. 用口语短句，每句不超过12个字，节奏快，停顿自然
4. 可以用"诶"、"卧槽"、"真的假的"、"笑死"这种词增加幽默感
5. 避免：据悉、数据显示、根据XX、首先其次最后、郑重声明、官话套话
6. 内容要有信息增量，要么你知道的他不知道，要么你知道但理解错的
7. 结尾干脆利落，直接说完就停，不要"今天就到这里"、"喜欢就点个赞"
8. 直接输出文案，一气呵成，不要前缀不要注释不要空行

口播文案："""
        for attempt in range(self.retry):
            try:
                client = self._client()
                msg = client.messages.create(
                    model=self.model,
                    max_tokens=400,
                    temperature=1.0,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = self._extract_text(msg).strip()
                # 清理可能残留的thinking痕迹和空行
                text = re.sub(r'\n+', '', text)
                text = re.sub(r'^[\s\W]+', '', text)
                if len(text) >= 80:
                    return text
            except Exception:
                pass
        return ""