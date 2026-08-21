"""上游数据源熔断守卫 (进程级, 线程安全)。

根本问题: 数据源 IP 被封/限流时, 每次直连调用烧满超时×retry (4-8s × 2-3 次),
30 路并发把线程池钉死, to_thread() 排队 120s → server 假死被 launchd 重启。

方案: 给绕过 lib_common 冷却的直连路径加逐级熔断 —
  连续 FAIL_THRESHOLD 次失败 → 熔断 (直接快速 fail, 0 阻塞), 冷却 300→3600s 逐级升级;
  冷却期满后下一次调用半开探测, 成功即恢复;
  同 host 并发 inflight 上限, 防止一批请求同时涌向被封源。
"""

import logging
import threading
import time
from contextlib import contextmanager

log = logging.getLogger("upstream_guard")
if not log.handlers:
    log.addHandler(logging.NullHandler())

COOLDOWN_LEVELS = [300, 600, 1200, 2400, 3600]
FAIL_THRESHOLD = 5        # 连续失败 N 次 → 熔断
MAX_INFLIGHT = 6          # 同 host 最大并发在飞请求 (腾讯/东财都限并发)


class SourceBusy(Exception):
    """同 host 并发超过上限, 调用方应快速跳过本轮。"""


_NAMES = {
    "em":       "东财直连(搜索/push2)",
    "tencent":  "腾讯直连(ifzq)",
    "ths":      "同花顺直连(d.10jqka)",
}

_state: dict[str, dict] = {}
_lock = threading.Lock()


def _get_locked(name: str) -> dict:
    """内部助手: 调用方必须已持有 _lock (threading.Lock 不可重入, 勿在持锁时二次加锁)."""
    return _state.setdefault(name, {
        "name": _NAMES.get(name, name),
        "calls": 0, "oks": 0, "fails": 0,
        "level": 0, "disabled_until": 0.0,
        "inflight": 0, "last_err": "",
    })


def should_skip(name: str) -> bool:
    """熔断中 → True, 调用方直接快速 fail 不上游。0 阻塞。"""
    with _lock:
        s = _state.get(name)
        return bool(s and time.time() < s["disabled_until"])


def remaining(name: str) -> float:
    with _lock:
        s = _state.get(name)
        return max(0.0, (s or {}).get("disabled_until", 0.0) - time.time())


def acquire(name: str) -> bool:
    """实调用前调用: 在飞并发达到上限 → False (调用方跳过本轮)。
    注意: 只有真正获得槽位才自增, 拒绝时不动 inflight (否则被拒请求泄漏 +1)。"""
    with _lock:
        s = _get_locked(name)
        if s["inflight"] >= MAX_INFLIGHT:
            return False
        s["inflight"] += 1
        return True


def release(name: str) -> None:
    with _lock:
        s = _state.get(name)
        if s:
            s["inflight"] = max(0, s["inflight"] - 1)


@contextmanager
def inflight(name: str):
    """限流窗口: 并发超上限抛 SourceBusy; 保证 release 不泄漏。"""
    if not acquire(name):
        raise SourceBusy(name)
    try:
        yield
    finally:
        release(name)


def record_ok(name: str) -> None:
    now = time.time()
    with _lock:
        s = _get_locked(name)
        s["calls"] += 1
        s["oks"] += 1
        s["fails"] = 0
        # 半开探测成功 / 冷却期后成功 → 提前恢复, 冷却等级回退
        if now < s["disabled_until"]:
            s["level"] = max(0, s["level"] - 1)
        s["disabled_until"] = 0.0


def record_fail(name: str, err: str = "") -> None:
    now = time.time()
    with _lock:
        s = _get_locked(name)
        s["calls"] += 1
        s["fails"] += 1
        s["oks"] = 0
        s["last_err"] = (err or "")[:200]
        if s["fails"] >= FAIL_THRESHOLD:
            level = min(s["level"], len(COOLDOWN_LEVELS) - 1)
            s["disabled_until"] = now + COOLDOWN_LEVELS[level]
            if s["fails"] >= FAIL_THRESHOLD + 10:
                s["level"] = min(s["level"] + 1, len(COOLDOWN_LEVELS) - 1)
            if s["fails"] == FAIL_THRESHOLD:
                log.warning(
                    f"upstream_guard 熔断 {name}: 连续 {s['fails']} 次失败, "
                    f"冷却 {COOLDOWN_LEVELS[level]}s (err={s['last_err']})"
                )


def snapshot() -> dict:
    """给 /api/metrics 观察。"""
    now = time.time()
    with _lock:
        return {
            name: {
                "name": s["name"],
                "calls": s["calls"],
                "oks": s["oks"],
                "fails": s["fails"],
                "level": s["level"],
                "open": now < s["disabled_until"],
                "remaining_s": round(max(0.0, s["disabled_until"] - now), 1),
                "inflight": s["inflight"],
                "last_err": s["last_err"],
            }
            for name, s in sorted(_state.items())
        }


def reset() -> None:
    with _lock:
        _state.clear()
