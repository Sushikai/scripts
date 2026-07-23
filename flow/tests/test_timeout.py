"""timeout 中间件单元测试。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_whitelist():
    from backend.middleware.timeout import _is_whitelisted
    assert _is_whitelisted("/api/job/abc/stream") is True
    assert _is_whitelisted("/api/backtest/run") is True
    assert _is_whitelisted("/api/stream/foo") is True
    assert _is_whitelisted("/api/dashboard") is False
    assert _is_whitelisted("/health") is False