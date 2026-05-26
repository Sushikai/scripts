#!/usr/bin/env python3
"""
MoneyPrinterTurbo 素材供给模块
从素材库抽取内容，生成视频创作 prompt
支持：火花宝宝（萌娃）/ 不存在的小镇（荒诞）
"""

from __future__ import annotations

import json
import random
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ============ 路径配置 ============

PROJECT_ROOT = Path(__file__).parent.parent
MATERIALS_DIR = PROJECT_ROOT / "materials"
PROCESSED_DIR = MATERIALS_DIR / "processed"
CHROMA_DB_PATH = PROJECT_ROOT / "database" / "chroma_db"


# ============ 风格模板 ============

STYLE_TEMPLATES = {
    "火花宝宝": {
        "video_prompt": (
            "Pure visual relaxing montage, baby/kid cute moments, "
            "warm and healing atmosphere, soft lighting, "
            "no text no narration, only visual storytelling. "
            "Style: {mood}"
        ),
        "bgm_keywords": ["温馨", "治愈", "轻柔", "可爱", "儿童", "亲子"],
        "example_tags": ["宝宝吃饭", "宝宝睡觉", "萌娃表情", "亲子互动", "可爱瞬间"],
    },
    "不存在的小镇": {
        "video_prompt": (
            "Surreal fantasy adventure, strange mysterious town, "
            "dreamlike atmosphere, whimsical and absurd, "
            "no text no narration, only visual storytelling. "
            "Style: {mood}"
        ),
        "bgm_keywords": ["奇幻", "神秘", "梦幻", "诡异", "探险", "魔法"],
        "example_tags": ["奇幻探险", "梦境故事", "魔法世界", "神秘小镇", "荒诞日常"],
    },
    "通用": {
        "video_prompt": (
            "Pure visual relaxing montage, no text no narration, "
            "only visual storytelling. Style: {mood}"
        ),
        "bgm_keywords": ["舒缓", "轻柔", "放松"],
        "example_tags": ["治愈", "风景", "生活"],
    },
}


# ============ MPT 供给器 ============

class MoneyPrinterFeeder:
    """
    给 MoneyPrinterTurbo 供给素材
    从 SQLite + Chroma 中抽取符合风格的素材
    生成视频 prompt 和 BGM 搜索关键词
    """

    def __init__(
        self,
        db_path: str | Path = None,
        chroma_path: str | Path = None,
    ):
        self.db_path = Path(db_path) if db_path else PROJECT_ROOT / "database" / "materials.db"
        self.chroma_path = Path(chroma_path) if chroma_path else CHROMA_DB_PATH
        self._db = None

    @property
    def db(self):
        """懒加载数据库"""
        if self._db is None:
            from database.materials_db import MaterialDatabase
            self._db = MaterialDatabase(self.db_path)
        return self._db

    def get_random_materials(
        self,
        category: str = "火花宝宝",
        count: int = 10,
        min_length: int = 20,
    ) -> list[dict]:
        """
        获取随机素材（用于视频创作）
        - category: 火花宝宝 / 不存在的小镇 / 通用
        - count: 返回数量
        - min_length: 文字最小长度
        """
        materials = self.db.get_materials_by_category(category, limit=100)
        # 过滤
        filtered = [
            m for m in materials
            if len(m.get("clean_text", "")) >= min_length
            and m.get("usable", True) == 1
        ]
        # 随机抽样
        random.shuffle(filtered)
        return filtered[:count]

    def generate_video_prompt(
        self,
        category: str = "火花宝宝",
        mood: Optional[str] = None,
        template: Optional[str] = None,
    ) -> str:
        """
        生成视频创作 prompt
        用于输入 MoneyPrinterTurbo
        """
        template = template or STYLE_TEMPLATES.get(category, STYLE_TEMPLATES["通用"])

        # 随机选取一个心情描述
        if not mood:
            mood_pool = ["温馨治愈", "可爱萌趣", "温暖感人", "轻快活泼"]
            if category == "不存在的小镇":
                mood_pool = ["神秘奇幻", "荒诞幽默", "梦境感", "诡异科幻"]
            mood = random.choice(mood_pool)

        prompt = template["video_prompt"].format(mood=mood)

        # 添加素材关键词
        materials = self.get_random_materials(category, count=5)
        if materials:
            tags = []
            for m in materials:
                t = m.get("tags", "")
                if isinstance(t, str) and t:
                    try:
                        tags.extend(json.loads(t))
                    except:
                        tags.append(t)
            if tags:
                unique_tags = list(set(tags))[:8]
                prompt += f"\n\n参考元素: {', '.join(unique_tags)}"

        return prompt

    def generate_bgm_keywords(self, category: str = "火花宝宝") -> list[str]:
        """
        生成 BGM 搜索关键词
        用于 yt-dlp 下载 BGM
        """
        template = STYLE_TEMPLATES.get(category, STYLE_TEMPLATES["通用"])
        return template["bgm_keywords"]

    def generate_config_snippet(
        self,
        category: str = "火花宝宝",
        theme_keyword: str = "",
    ) -> dict:
        """
        生成 config.toml 片段
        用于 MoneyPrinterTurbo 配置
        """
        prompt = self.generate_video_prompt(category)
        bgm_keywords = self.generate_bgm_keywords(category)

        return {
            "subtitle_provider": "none",  # 关闭字幕
            "video_prompt": prompt,
            "theme_keywords": theme_keyword or bgm_keywords[0] if bgm_keywords else "",
            "video_length": 60,  # 秒
            "voice_provider": "none",  # 无旁白
            "bgm_provider": "local",  # 本地 BGM
            "transition_style": "smooth",  # 舒缓过渡
        }

    def feed_to_mpt(
        self,
        mpt_config_path: str | Path,
        category: str = "火花宝宝",
        **override_kwargs,
    ) -> bool:
        """
        直接写入 MoneyPrinterTurbo 的 config.toml
        mpt_config_path: MoneyPrinterTurbo 的 config.toml 路径
        """
        snippet = self.generate_config_snippet(category, **override_kwargs)

        try:
            config_path = Path(mpt_config_path)
            if not config_path.exists():
                logger.warning(f"config.toml 不存在，创建新文件: {config_path}")
                config_path.parent.mkdir(parents=True, exist_ok=True)

            # 读取现有配置
            existing = {}
            if config_path.exists():
                import tomli
                try:
                    with open(config_path, "rb") as f:
                        existing = tomli.load(f)
                except:
                    pass

            # 合并
            merged = {**existing, **snippet}

            # 写回
            import tomli_w
            with open(config_path, "wb") as f:
                tomli_w.dump(merged, f)

            logger.info(f"✅ 已写入配置到 {config_path}")
            return True

        except ImportError as e:
            logger.warning(f"tomli/tomli_w 未安装，跳过直接写入: {e}")
            # 输出配置供手动复制
            print("\n" + "=" * 60)
            print("配置片段（请手动复制到 config.toml）:")
            print("=" * 60)
            print(json.dumps(snippet, ensure_ascii=False, indent=2))
            print("=" * 60)
            return False

        except Exception as e:
            logger.error(f"写入配置失败: {e}")
            return False

    def batch_export_for_mpt(
        self,
        output_dir: str | Path,
        count: int = 10,
        categories: list[str] = None,
    ):
        """
        批量导出素材供 MoneyPrinterTurbo 使用
        生成多个 JSON 文件，每个代表一个视频配置
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        categories = categories or ["火花宝宝", "不存在的小镇"]
        results = {}

        for cat in categories:
            materials = self.get_random_materials(cat, count=count)
            if not materials:
                continue

            # 合并素材文字
            combined_text = "\n".join([m.get("clean_text", "") for m in materials if m.get("clean_text")])

            prompt = self.generate_video_prompt(cat)
            bgm_keywords = self.generate_bgm_keywords(cat)

            config = {
                "category": cat,
                "video_prompt": prompt,
                "bgm_keywords": bgm_keywords,
                "source_materials": [
                    {"id": m.get("id"), "text": m.get("clean_text", "")[:100]}
                    for m in materials
                ],
                "combined_text": combined_text[:500],
            }

            filename = f"{cat}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            output_path = output_dir / filename

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)

            results[cat] = str(output_path)
            logger.info(f"导出 {cat}: {output_path}")

        return results


# ============ 快速函数 ============

def quick_feed(category: str = "火花宝宝") -> dict:
    """一键生成 MPT 配置"""
    feeder = MoneyPrinterFeeder()
    return feeder.generate_config_snippet(category)


# ---- 单元测试 ----
if __name__ == "__main__":
    from datetime import datetime

    print("MoneyPrinterTurbo 素材供给模块已加载")
    print()

    feeder = MoneyPrinterFeeder()

    # 测试生成配置
    for cat in ["火花宝宝", "不存在的小镇"]:
        print(f"【{cat}】配置:")
        config = feeder.generate_config_snippet(cat)
        for k, v in config.items():
            print(f"  {k}: {v}")
        print()

    # 测试素材获取
    print("【素材抽样】:")
    materials = feeder.get_random_materials("火花宝宝", count=3)
    for m in materials:
        print(f"  - {m.get('clean_text', '')[:50]}...")
    print()

    print("✅ 模块测试通过")