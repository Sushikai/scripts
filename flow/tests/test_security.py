"""security 工具单元测试。"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _tmp_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="flow_sec_"))


def test_safe_path_under_allows_in_root():
    from backend.security import safe_path_under
    root = _tmp_root()
    sub = root / "sub" / "file.mp4"
    sub.parent.mkdir(parents=True)
    sub.write_bytes(b"x")
    got = safe_path_under((str(root),), str(sub))
    assert got == sub.resolve()


def test_safe_path_under_blocks_traversal():
    from backend.security import safe_path_under
    from fastapi import HTTPException
    root = _tmp_root()
    other = Path(tempfile.mkdtemp(prefix="flow_other_"))
    bad = other / "secret.txt"
    bad.write_text("secret")
    try:
        safe_path_under((str(root),), str(bad))
        assert False, "should have raised"
    except HTTPException as e:
        assert e.status_code == 403
        assert e.detail["code"] == "FILE_FORBIDDEN"


def test_safe_path_under_blocks_parent_traversal():
    """.. 跳出也应被挡。"""
    from backend.security import safe_path_under
    from fastapi import HTTPException
    root = _tmp_root()
    sub = root / "x"
    sub.mkdir()
    # 模拟 ../attack
    attack = str(sub / ".." / ".." / "etc" / "passwd")
    try:
        safe_path_under((str(root),), attack)
        assert False, "should have raised"
    except HTTPException as e:
        assert e.status_code == 403


def test_safe_path_under_multiple_roots():
    """多个白名单根:命中任一即放行。"""
    from backend.security import safe_path_under
    r1 = _tmp_root()
    r2 = _tmp_root()
    target = r2 / "ok.mp4"
    target.touch()
    got = safe_path_under((str(r1), str(r2)), str(target))
    assert got == target.resolve()


def test_client_ip_xff():
    from backend.security import client_ip
    from starlette.requests import Request

    scope = {"type": "http", "headers": [(b"x-forwarded-for", b"1.2.3.4, 10.0.0.1")], "client": ("127.0.0.1", 80)}
    req = Request(scope)
    assert client_ip(req) == "1.2.3.4"


def test_client_ip_direct():
    from backend.security import client_ip
    from starlette.requests import Request

    scope = {"type": "http", "headers": [], "client": ("5.6.7.8", 80)}
    req = Request(scope)
    assert client_ip(req) == "5.6.7.8"


def test_require_local_token_missing():
    from backend.security import require_local_token
    from fastapi import HTTPException
    # env 没设 LOCAL_TOKEN,默认 "flow-local-dev-token"
    try:
        require_local_token(None)
        assert False, "should have raised"
    except HTTPException as e:
        assert e.status_code == 401


def test_require_local_token_valid():
    from backend.security import require_local_token
    require_local_token("flow-local-dev-token")  # 不应抛


def test_require_local_token_bearer():
    from backend.security import require_local_token
    require_local_token("Bearer flow-local-dev-token")  # 不应抛


def test_require_local_token_invalid():
    from backend.security import require_local_token
    from fastapi import HTTPException
    try:
        require_local_token("wrong-token")
        assert False, "should have raised"
    except HTTPException as e:
        assert e.status_code == 401


def test_is_under():
    from backend.security import is_under
    root = _tmp_root()
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    assert is_under(root.resolve(), sub.resolve()) is True
    other = Path(tempfile.mkdtemp(prefix="flow_o_"))
    assert is_under(root.resolve(), other.resolve()) is False