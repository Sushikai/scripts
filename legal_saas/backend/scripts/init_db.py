"""
手动初始化数据库。
用法: python backend/scripts/init_db.py
"""
from __future__ import annotations
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.db.database import init_schema, query  # noqa: E402
from app.config import DATA_DIR  # noqa: E402

MIGRATIONS_DIR = BACKEND_DIR / "app" / "db" / "migrations"


def main():
    print(f"[init_db] DB: {DATA_DIR / 'db.sqlite'}")
    init_schema(MIGRATIONS_DIR)
    tables = query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    print(f"[init_db] ✓ {len(tables)} 张表创建成功:")
    for t in tables:
        print(f"   - {t['name']}")


if __name__ == "__main__":
    main()