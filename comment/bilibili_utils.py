#!/usr/bin/env python3
"""
B站公共工具模块
所有B站相关脚本共享的工具函数
"""

import fcntl, json, random, time
from pathlib import Path
from typing import Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ==================== 共享 Session 配置 ====================
def make_session(retries: int = 3, backoff: float = 1.5) -> requests.Session:
    """创建配置好重试机制的 requests Session"""
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
    """原子写入：先写临时文件再rename，防止数据损坏"""
    tmp = path.with_suffix('.tmp')
    tmp.write_text(data, encoding='utf-8')
    tmp.replace(path)

# ==================== 智能截断 ====================
def smart_truncate(text: str, max_len: int) -> str:
    """在分句标点附近截断，保证句子完整性"""
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

# ==================== 锁文件管理（带PID检查）====================
class LockFile:
    """带PID检查的锁文件，防止进程崩溃后永久阻塞"""
    
    def __init__(self, lock_path: Path, timeout_check: bool = True):
        self.lock_path = lock_path
        self.lock_fd = None
        self.timeout_check = timeout_check
    
    def acquire(self) -> bool:
        """尝试获取锁，返回True表示成功，False表示已被锁定"""
        import os
        self.lock_fd = open(self.lock_path, 'w')
        try:
            fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_fd.write(str(os.getpid()))
            self.lock_fd.flush()
            
            # PID检查：验证锁文件中的PID是否还存活
            if self.timeout_check:
                self._check_stale_lock()
            return True
        except BlockingIOError:
            # 锁被占用，检查是否是僵尸锁
            self.lock_fd.close()
            if self.timeout_check and self._check_stale_lock():
                # 重试一次
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
        """检查锁文件是否过期（进程已崩溃）"""
        import os
        import signal
        try:
            pid_str = self.lock_path.read_text().strip()
            if pid_str:
                pid = int(pid_str)
                # 检查进程是否存活
                try:
                    os.kill(pid, 0)  # 不发送信号，只检测
                    return False  # 进程还活着，锁有效
                except OSError:
                    # 进程不存在，锁已过期
                    return True
        except:
            return True
    
    def release(self):
        """释放锁"""
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
    """评论冷却管理器"""
    
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
        """等待冷却时间，返回实际等待秒数"""
        last_ts = self.get_last_ts()
        elapsed = time.time() - last_ts
        if elapsed < self.min_interval:
            wait_time = self.min_interval - elapsed + random.uniform(1, 5)
            time.sleep(wait_time)
            return wait_time
        return 0.0

# ==================== 视频Title缓存 ====================
class VideoTitleCache:
    """视频标题缓存，避免重复请求API"""
    
    def __init__(self, cache_file: Path, ttl: int = 3600):
        self.cache_file = cache_file
        self.ttl = ttl  # 缓存有效期（秒）
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
        """获取视频标题，先查缓存，缓存失效则请求API"""
        now = time.time()
        if aid in self._cache:
            cached = self._cache[aid]
            if now - cached['ts'] < self.ttl:
                return cached['title']
        
        # 缓存失效，请求API
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
        """批量预取视频标题"""
        uncached = [aid for aid in aids if aid not in self._cache or time.time() - self._cache[aid]['ts'] >= self.ttl]
        if not uncached:
            return
        
        for aid in uncached[:20]:  # 最多批量请求20个
            self.get(aid, session, cookies, headers)
            time.sleep(0.1)  # 避免请求过快

# ==================== 对话历史缓存 ====================
class ConversationCache:
    """对话历史缓存，支持多轮对话"""
    
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
        # 保留最近N条消息
        self._cache[key] = self._cache[key][-self.max_messages:]
        self._save()
    
    def build_prompt(self, key: str, uname: str, current_comment: str, video_title: str = "", parent_comment: str = "") -> str:
        """构建带历史上下文的prompt"""
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

# ==================== 并发请求工具 ====================
def fetch_parallel(urls: list, session: requests.Session, cookies: dict, headers: dict, 
                   max_workers: int = 5, timeout: int = 10) -> list:
    """并发请求多个URL，返回结果列表"""
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

# ==================== WBI签名工具 ====================
MIXIN_KEY_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 20, 44, 54, 28, 14, 34, 56, 4, 25, 63, 57, 62, 51, 30,
    36, 24, 6, 64, 46, 11, 60, 51, 30, 36, 24, 6, 64, 46, 11, 60, 51, 30, 36,
    24, 6, 64, 46, 11, 60, 51, 30, 36, 24, 6, 64, 46, 11, 60, 51, 30, 36, 24,
    6, 64, 46, 11, 60
]

def mixin_key(orig: str) -> str:
    """WBI key 混淆"""
    result = []
    for i in MIXIN_KEY_TAB:
        if i < len(orig):
            result.append(orig[i])
    return ''.join(result)

def get_wbi_sign(params: dict, img_key: str, sub_key: str) -> str:
    """生成 WBI 签名"""
    import hashlib
    from urllib.parse import urlencode
    
    mil = mixin_key(img_key + sub_key)
    half_len = len(mil) // 2
    query_str = urlencode(sorted(params.items()), safe='/:?=')
    sign_str = mil[:half_len] + query_str + mil[half_len:]
    return hashlib.md5(sign_str.encode()).hexdigest()

# ==================== 限速检测 ====================
RATE_LIMIT_CODES = {'12014', '12068', '12069', '12015', '12016'}

def is_rate_limit_error(code: int, message: str) -> bool:
    """检测是否是限速错误"""
    if str(code) in RATE_LIMIT_CODES:
        return True
    return any(keyword in message for keyword in ['cd时间未到', '操作太频繁', '重复评论'])

# ==================== 通用日志 ====================
def log(msg: str, log_file: Optional[Path] = None):
    """统一日志格式"""
    import sys
    ts = time.strftime('%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    if log_file:
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except:
            pass
