"""trace_id 中间件单元测试。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_generates_trace_id():
    from backend.middleware.trace import TraceIdMiddleware
    import secrets
    # 中间件本身只暴露 HEADER 常量,实际验证通过 /health
    assert TraceIdMiddleware.HEADER == "X-Trace-Id"
    # 生成 token_hex(4) 应为 8 hex 字符
    t = secrets.token_hex(4)
    assert len(t) == 8
    assert int(t, 16) >= 0


def test_trace_id_validation_via_server(client):
    """合法 trace_id 回显;非法字符忽略重生成。"""
    r1 = client.get("/health", headers={"X-Trace-Id": "abcd1234"})
    assert r1.headers["X-Trace-Id"] == "abcd1234"
    # 非法字符(包含特殊符号)应被忽略,服务端生成新 8 字节 hex
    r2 = client.get("/health", headers={"X-Trace-Id": "???bad????"})
    tid = r2.headers["X-Trace-Id"]
    assert len(tid) == 8
    assert all(c in "0123456789abcdef" for c in tid)