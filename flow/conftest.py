"""flow 项目根 conftest:设 FLOW_PORT + 临时 DB。"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 用临时 DB,避免污染真实数据
_TMP_DB_DIR = Path(tempfile.mkdtemp(prefix="flow_test_"))
os.environ.setdefault("FLOW_DB", str(_TMP_DB_DIR / "flow.db"))
os.environ.setdefault("FLOW_CACHE_DB", str(_TMP_DB_DIR / "cache.db"))
os.environ.setdefault("FLOW_ACCESS_LOG", str(_TMP_DB_DIR / "access.log"))
os.environ.setdefault("FLOW_PORT", "8811")  # 测试用 8811,避开真实 8810