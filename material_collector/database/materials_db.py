#!/usr/bin/env python3
"""
数据库模块 - SQLite + JSON 混合存储
表结构：materials / processed_materials / statistics
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ============ 数据库路径 ============
DB_PATH = Path(__file__).parent.parent / "database" / "materials.db"


# ============ SQLite 连接管理 ============

@contextmanager
def get_db_connection(db_path: str | Path = DB_PATH):
    """获取数据库连接的上下文管理器"""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database(db_path: str | Path = DB_PATH):
    """初始化数据库表结构"""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        # 原始素材表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS materials (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                keyword TEXT,
                content_type TEXT DEFAULT 'subtitle',
                raw_text TEXT NOT NULL,
                video_title TEXT DEFAULT '',
                video_bvid TEXT DEFAULT '',
                timestamp TEXT,
                source_url TEXT DEFAULT '',
                ad_score REAL DEFAULT 0.0,
                content_hash TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                processed INTEGER DEFAULT 0
            )
        """)

        # 处理后素材表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_materials (
                id TEXT PRIMARY KEY,
                original_id TEXT,
                platform TEXT NOT NULL,
                keyword TEXT,
                clean_text TEXT NOT NULL,
                category TEXT DEFAULT '通用',
                mood TEXT DEFAULT '',
                tags TEXT,  -- JSON 数组
                usable INTEGER DEFAULT 1,
                reason TEXT DEFAULT '',
                suggestion TEXT DEFAULT '',
                vector_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (original_id) REFERENCES materials(id)
            )
        """)

        # 统计表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS statistics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                platform TEXT,
                category TEXT,
                collected_count INTEGER DEFAULT 0,
                processed_count INTEGER DEFAULT 0,
                unique_count INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 关键词黑名单（去重）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS keyword_blacklist (
                keyword TEXT PRIMARY KEY,
                reason TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 索引
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_materials_hash ON materials(content_hash)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_materials_platform ON materials(platform)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_materials_timestamp ON materials(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_processed_category ON processed_materials(category)")

        conn.commit()
        logger.info(f"数据库初始化完成: {db_path}")


# ============ 数据库操作类 ============

class MaterialDatabase:
    """素材数据库操作类"""

    def __init__(self, db_path: str | Path = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        init_database(self.db_path)

    # ---- 原始素材操作 ----

    def insert_material(self, material: dict) -> bool:
        """插入原始素材"""
        try:
            with get_db_connection(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR IGNORE INTO materials
                    (id, platform, keyword, content_type, raw_text, video_title,
                     video_bvid, timestamp, source_url, ad_score, content_hash, processed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """, (
                    material.get("id"),
                    material.get("platform"),
                    material.get("keyword", ""),
                    material.get("content_type", "subtitle"),
                    material.get("raw_text"),
                    material.get("video_title", ""),
                    material.get("video_bvid", ""),
                    material.get("timestamp", datetime.now().isoformat()),
                    material.get("source_url", ""),
                    material.get("ad_score", 0.0),
                    material.get("hash", ""),
                ))
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"插入素材失败: {e}")
            return False

    def insert_materials_batch(self, materials: list[dict]) -> int:
        """批量插入素材，返回插入数量"""
        count = 0
        for m in materials:
            if self.insert_material(m):
                count += 1
        return count

    def get_unprocessed_materials(self, limit: int = 100) -> list[dict]:
        """获取未处理的素材"""
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM materials
                WHERE processed = 0
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            # sqlite3.Row is not JSON serializable; convert to plain dict
            result = []
            for row in rows:
                d = dict(row)
                # Ensure datetime fields are JSON-serializable strings
                for key, value in d.items():
                    if hasattr(value, "isoformat"):
                        d[key] = value.isoformat()
                    elif value is None:
                        d[key] = ""
                result.append(d)
            return result

    def mark_processed(self, material_id: str):
        """标记素材已处理"""
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE materials SET processed = 1 WHERE id = ?", (material_id,))

    # ---- 处理后素材操作 ----

    def insert_processed(self, processed: dict) -> bool:
        """插入处理后的素材"""
        try:
            with get_db_connection(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO processed_materials
                    (id, original_id, platform, keyword, clean_text, category, mood,
                     tags, usable, reason, suggestion, vector_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    processed.get("id"),
                    processed.get("original_id"),
                    processed.get("platform"),
                    processed.get("keyword", ""),
                    processed.get("clean_text"),
                    processed.get("category", "通用"),
                    processed.get("mood", ""),
                    json.dumps(processed.get("tags", []), ensure_ascii=False),
                    1 if processed.get("usable", True) else 0,
                    processed.get("reason", ""),
                    processed.get("suggestion", ""),
                    processed.get("vector_id", ""),
                ))
                return True
        except Exception as e:
            logger.error(f"插入处理素材失败: {e}")
            return False

    def get_materials_by_category(self, category: str, limit: int = 50) -> list[dict]:
        """按分类获取素材"""
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM processed_materials
                WHERE category = ? AND usable = 1
                ORDER BY created_at DESC
                LIMIT ?
            """, (category, limit))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def search_materials(self, keyword: str, category: Optional[str] = None) -> list[dict]:
        """搜索素材"""
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            if category:
                cursor.execute("""
                    SELECT * FROM processed_materials
                    WHERE (clean_text LIKE ? OR tags LIKE ?) AND category = ?
                    ORDER BY created_at DESC
                    LIMIT 50
                """, (f"%{keyword}%", f"%{keyword}%", category))
            else:
                cursor.execute("""
                    SELECT * FROM processed_materials
                    WHERE clean_text LIKE ? OR tags LIKE ?
                    ORDER BY created_at DESC
                    LIMIT 50
                """, (f"%{keyword}%", f"%{keyword}%"))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    # ---- 统计操作 ----

    def update_statistics(self, date: Optional[str] = None):
        """更新每日统计"""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()

            for platform in ["douyin", "bilibili", "xiaohongshu"]:
                cursor.execute("""
                    SELECT COUNT(*) FROM materials
                    WHERE platform = ? AND date(timestamp) = ?
                """, (platform, date))
                collected = cursor.fetchone()[0]

                cursor.execute("""
                    SELECT COUNT(DISTINCT content_hash) FROM materials
                    WHERE platform = ? AND date(timestamp) = ?
                """, (platform, date))
                unique = cursor.fetchone()[0]

                cursor.execute("""
                    SELECT COUNT(*) FROM processed_materials pm
                    JOIN materials m ON pm.original_id = m.id
                    WHERE m.platform = ? AND date(m.timestamp) = ?
                """, (platform, date))
                processed = cursor.fetchone()[0]

                cursor.execute("""
                    INSERT OR REPLACE INTO statistics
                    (date, platform, collected_count, processed_count, unique_count, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (date, platform, collected, processed, unique))

    def get_statistics(self, days: int = 7) -> list[dict]:
        """获取最近N天统计"""
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM statistics
                WHERE date >= date('now', ?)
                ORDER BY date DESC
            """, (f"-{days} days",))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def get_category_distribution(self) -> dict:
        """获取分类分布"""
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT category, COUNT(*) as count
                FROM processed_materials
                WHERE usable = 1
                GROUP BY category
            """)
            return {row["category"]: row["count"] for row in cursor.fetchall()}

    # ---- 去重操作 ----

    def is_duplicate_hash(self, content_hash: str, window_hours: int = 24) -> bool:
        """检查是否在时间窗口内重复"""
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM materials
                WHERE content_hash = ?
                AND timestamp >= datetime('now', ?)
            """, (content_hash, f"-{window_hours} hours"))
            return cursor.fetchone()[0] > 0

    def get_recent_materials(self, hours: int = 24, platform: Optional[str] = None) -> list[dict]:
        """获取最近N小时的素材"""
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            if platform:
                cursor.execute("""
                    SELECT * FROM materials
                    WHERE timestamp >= datetime('now', ?) AND platform = ?
                    ORDER BY timestamp DESC
                """, (f"-{hours} hours", platform))
            else:
                cursor.execute("""
                    SELECT * FROM materials
                    WHERE timestamp >= datetime('now', ?)
                    ORDER BY timestamp DESC
                """, (f"-{hours} hours",))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]


# ---- 快速查询函数 ----

_db_instance = None

def get_database(db_path: str | Path = DB_PATH) -> MaterialDatabase:
    """获取数据库实例（单例）"""
    global _db_instance
    if _db_instance is None:
        _db_instance = MaterialDatabase(db_path)
    return _db_instance


# ---- 单元测试 ----
if __name__ == "__main__":
    print("数据库模块已加载")
    print(f"数据库路径: {DB_PATH}")

    db = MaterialDatabase()
    print("\n当前统计:")
    print(db.get_category_distribution())

    print("\n最近24小时采集:")
    recent = db.get_recent_materials(hours=24)
    print(f"  共 {len(recent)} 条")

    print("\n✅ 数据库模块测试通过")