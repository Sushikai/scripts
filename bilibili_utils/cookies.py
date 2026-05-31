"""
B站 Cookie 加载工具
统一所有 B站相关脚本的 cookie 加载逻辑
"""
import json
from pathlib import Path
from typing import Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ==================== Cookie 文件路径 ====================
ALL_COOKIE_FILES = [
    Path("/Users/kaikai/scripts/20岁还没赚够100w_cookies.txt"),
    Path("/Users/kaikai/scripts/20岁还没开始环球旅行_cookies.txt"),
    Path("/Users/kaikai/scripts/tiktok_story_bili/那那天下雨了_cookies.txt"),
    Path("/Users/kaikai/scripts/tiktok_story_bili/风走了叶落_cookies.txt"),
]

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
}

def _load_json_cookies(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(data, dict) and 'SESSDATA' in data:
            return data
        if isinstance(data, list):
            return {c['name']: c['value'] for c in data if 'name' in c and 'value' in c}
    except Exception:
        pass
    return {}

def _load_netscape_cookies(path: Path) -> dict:
    cookies = {}
    try:
        for line in path.read_text(encoding='utf-8').split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]
    except Exception:
        pass
    return cookies

def _load_cookies_from_file(path: Path) -> dict:
    try:
        text = path.read_text(encoding='utf-8').strip()
        if text.startswith('{') or text.startswith('['):
            return _load_json_cookies(path)
        else:
            return _load_netscape_cookies(path)
    except Exception:
        return {}

def validate_account(cookies: dict, timeout: int = 8) -> bool:
    if not cookies.get("SESSDATA"):
        return False
    try:
        r = requests.get(
            "https://api.bilibili.com/x/web-interface/nav",
            cookies=cookies,
            headers=DEFAULT_HEADERS,
            timeout=timeout
        )
        j = r.json()
        return j.get("code") == 0 and j.get("data", {}).get("isLogin") == True
    except Exception:
        return False

def load_all_accounts(cookie_files: list[Path] | None = None) -> list[dict]:
    """加载所有有效账号，返回账号列表"""
    files = cookie_files or ALL_COOKIE_FILES
    accounts = []
    for i, path in enumerate(files):
        cookies = _load_cookies_from_file(path)
        if not cookies.get("SESSDATA"):
            print(f"  账号{i+1} [{path.name}] 无SESSDATA，跳过")
            continue
        if not validate_account(cookies):
            print(f"  账号{i+1} [{path.name}] 验证失败，跳过")
            continue
        uname = ""
        try:
            r = requests.get("https://api.bilibili.com/x/web-interface/nav", cookies=cookies, timeout=8)
            uname = r.json().get("data", {}).get("uname", path.stem[:10])
        except Exception:
            uname = path.stem[:10]
        print(f"  账号{i+1} [{uname}] 加载成功")
        session = requests.Session()
        session.mount('https://', HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1.5, status_forcelist={429, 500, 502, 503, 504})))
        accounts.append({
            "name": path.stem[:15],
            "cookies": cookies,
            "sessdata": cookies.get("SESSDATA", ""),
            "bili_jct": cookies.get("bili_jct", ""),
            "buvid3": cookies.get("buvid3", ""),
            "session": session,
        })
    return accounts