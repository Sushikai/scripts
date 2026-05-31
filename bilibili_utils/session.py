"""
B站 HTTP Session 和通用工具
"""
import fcntl, json, random, time
from datetime import datetime
from pathlib import Path
from typing import Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ==================== Session ====================
def make_session(retries: int = 3, backoff: float = 1.5) -> requests.Session:
    session = requests.Session()
    session.mount(
        'https://',
        HTTPAdapter(
            max_retries=Retry(
                total=retries,
                backoff_factor=backoff,
                status_forcelist={429, 500, 502, 503, 504}
            )
        )
    )
    return session

# ==================== 原子写入 ====================
def atomic_write(path: Path, data: str) -> None:
    tmp = path.with_suffix('.tmp')
    tmp.write_text(data, encoding='utf-8')
    tmp.replace(path)

# ==================== 智能截断 ====================
def smart_truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    punct = '。！？；\n'
    cutoff = max_len
    for i in range(max_len - 1, max(max_len - 15, 0), -1):
        if text[i] in punct:
            cutoff = i + 1
            break
    result = text[:cutoff]
    if cutoff < len(text) - 1 and result[-1] not in '。！？':
        result = result.rstrip('，、；') + '…'
    elif result[-1] not in '。！？…':
        result = result.rstrip('，、；') + '…'
    return result

# ==================== 锁文件管理 ====================
class LockFile:
    def __init__(self, lock_path: Path, timeout_check: bool = True):
        self.lock_path = lock_path
        self.lock_fd = None
        self.timeout_check = timeout_check

    def acquire(self) -> bool:
        import os
        self.lock_fd = open(self.lock_path, 'w')
        try:
            fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_fd.write(str(os.getpid()))
            self.lock_fd.flush()
            if self.timeout_check:
                self._check_stale_lock()
            return True
        except BlockingIOError:
            self.lock_fd.close()
            if self.timeout_check and self._check_stale_lock():
                self.lock_fd = open(self.lock_path, 'w')
                try:
                    fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.lock_fd.write(str(os.getpid()))
                    self.lock_fd.flush()
                    return True
                except BlockingIOError:
                    self.lock_fd.close()
                    return False
            return False

    def _check_stale_lock(self) -> bool:
        import os
        import signal
        try:
            pid_str = self.lock_path.read_text().strip()
            if pid_str:
                pid = int(pid_str)
                try:
                    os.kill(pid, 0)
                    return False
                except OSError:
                    return True
        except:
            return True

    def release(self):
        if self.lock_fd:
            try:
                fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_UN)
                self.lock_fd.close()
            except:
                pass
            self.lock_fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.release()

# ==================== 冷却控制 ====================
class CooldownManager:
    def __init__(self, min_interval: int, max_interval: int, store_file: Path):
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.store_file = store_file
        self._data = {}
        self._load()

    def _load(self):
        try:
            if self.store_file.exists():
                self._data = json.loads(self.store_file.read_text(encoding='utf-8'))
        except:
            self._data = {}

    def _save(self):
        try:
            atomic_write(self.store_file, json.dumps(self._data, ensure_ascii=False, indent=2))
        except:
            pass

    def get_last_ts(self) -> float:
        return self._data.get('last_comment_ts', 0)

    def record(self):
        self._data['last_comment_ts'] = time.time()
        self._save()

    def wait(self) -> float:
        last_ts = self.get_last_ts()
        elapsed = time.time() - last_ts
        if elapsed < self.min_interval:
            wait_time = self.min_interval - elapsed + random.uniform(1, 5)
            time.sleep(wait_time)
            return wait_time
        return 0.0

# ==================== 视频标题缓存 ====================
class VideoTitleCache:
    def __init__(self, cache_file: Path, ttl: int = 3600):
        self.cache_file = cache_file
        self.ttl = ttl
        self._cache = {}
        self._load()

    def _load(self):
        try:
            if self.cache_file.exists():
                self._cache = json.loads(self.cache_file.read_text(encoding='utf-8'))
        except:
            self._cache = {}

    def _save(self):
        try:
            atomic_write(self.cache_file, json.dumps(self._cache, ensure_ascii=False, indent=2))
        except:
            pass

    def get(self, aid: str, session: requests.Session, cookies: dict, headers: dict) -> str:
        now = time.time()
        if aid in self._cache:
            cached = self._cache[aid]
            if now - cached['ts'] < self.ttl:
                return cached['title']
        try:
            r = session.get(
                f"https://api.bilibili.com/x/web-interface/view?aid={aid}",
                headers=headers, cookies=cookies, timeout=8
            )
            data = r.json()
            if data.get('code') == 0:
                title = data.get('data', {}).get('title', '')
                self._cache[aid] = {'title': title, 'ts': now}
                self._save()
                return title
        except:
            pass
        return ""

    def prefetch(self, aids: list, session: requests.Session, cookies: dict, headers: dict):
        uncached = [aid for aid in aids if aid not in self._cache or time.time() - self._cache[aid]['ts'] >= self.ttl]
        if not uncached:
            return
        for aid in uncached[:20]:
            self.get(aid, session, cookies, headers)
            time.sleep(0.1)

# ==================== 对话历史缓存 ====================
class ConversationCache:
    def __init__(self, cache_file: Path, max_messages: int = 20):
        self.cache_file = cache_file
        self.max_messages = max_messages
        self._cache = {}
        self._load()

    def _load(self):
        try:
            if self.cache_file.exists():
                self._cache = json.loads(self.cache_file.read_text(encoding='utf-8'))
        except:
            self._cache = {}

    def _save(self):
        try:
            atomic_write(self.cache_file, json.dumps(self._cache, ensure_ascii=False, indent=2))
        except:
            pass

    def get(self, key: str) -> list:
        return self._cache.get(key, [])

    def add(self, key: str, user_msg: str, assistant_msg: str):
        if key not in self._cache:
            self._cache[key] = []
        self._cache[key].append({"role": "user", "content": user_msg})
        self._cache[key].append({"role": "assistant", "content": assistant_msg})
        self._cache[key] = self._cache[key][-self.max_messages:]
        self._save()

    def build_prompt(self, key: str, uname: str, current_comment: str, video_title: str = "", parent_comment: str = "") -> str:
        history = self.get(key)
        lines = []
        if video_title:
            lines.append(f"视频标题：{video_title}")
        if parent_comment and parent_comment != current_comment:
            lines.append(f"这条评论是在回复：「{parent_comment}」")
        lines.append(f"当前对话：")
        lines.append(f"粉丝「{uname}」：{current_comment}")
        if history:
            lines.append("")
            lines.append("--- 近期对话历史（按时间顺序）---")
            for msg in history[-12:]:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "user":
                    lines.append(f"  粉丝「{uname}」：{content}")
                else:
                    lines.append(f"  UP主（你）：{content}")
            lines.append("---")
            lines.append(f"现在你要继续回复粉丝「{uname}」，结合上面的对话上下文，自然地接着聊下去。")
        else:
            lines.append("（这是首次互动，没有对话历史）")
        return "\n".join(lines)

# ==================== 并发请求 ====================
def fetch_parallel(urls: list, session: requests.Session, cookies: dict, headers: dict,
                   max_workers: int = 5, timeout: int = 10) -> list:
    import concurrent.futures
    def fetch_one(url):
        try:
            r = session.get(url, cookies=cookies, headers=headers, timeout=timeout)
            return r.json()
        except:
            return None
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_one, url): url for url in urls}
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    return results

# ==================== 限速检测 ====================
RATE_LIMIT_CODES = {'12014', '12068', '12069', '12015', '12016'}

def is_rate_limit_error(code: int, message: str) -> bool:
    if str(code) in RATE_LIMIT_CODES:
        return True
    return any(keyword in message for keyword in ['cd时间未到', '操作太频繁', '重复评论'])

# ==================== 日志 ====================
def log(msg: str, log_file: Optional[Path] = None):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    if log_file:
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except:
            pass