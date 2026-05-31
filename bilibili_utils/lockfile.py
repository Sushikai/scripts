"""
LockFile 工具（独立导出，方便直接使用）
"""
import fcntl
import os
import signal
from pathlib import Path

class LockFile:
    """带PID检查的锁文件，防止进程崩溃后永久阻塞"""

    def __init__(self, lock_path: Path, timeout_check: bool = True):
        self.lock_path = lock_path
        self.lock_fd = None
        self.timeout_check = timeout_check

    def acquire(self) -> bool:
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