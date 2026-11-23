"""进程内并发控制：写事务串行 + 推理并发限流。

当前部署是单进程、多线程的演示服务：
- 所有写路径共用同一个 SQLite 写连接，多语句事务必须整段串行，不能只在单条
  SQL 上加锁，否则两个事务会交错到同一个连接上。
- Agent 推理会长时间占用大模型网关；多人同时点击「一键跑到底」时必须限流，
  否则请求全部排队到超时，看起来像服务挂了。
"""
from __future__ import annotations

import functools
import os
import threading
from contextlib import contextmanager


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        # .env 由 freight.agent 在首次调用大模型时才加载；本上限在更早的请求
        # 入口生效，因此这里只读这一个键，不提前加载整份 .env 影响测试隔离。
        env_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        try:
            with open(env_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith(f"{name}="):
                        value = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        except OSError:
            pass
    try:
        return max(1, int(value if value is not None else default))
    except (TypeError, ValueError):
        return default


# 全进程写事务锁。RLock 允许批量执行在外层持锁时继续调用 execute_action。
WRITE_LOCK = threading.RLock()

# 同时在跑的大模型推理链上限；演示环境默认 2，避免多人同时跑长链路打满网关。
REASONING_LIMIT = _env_int("OPENAI_CHAT_MAX_PARALLEL", 2)
_REASONING_SLOTS = threading.BoundedSemaphore(REASONING_LIMIT)


class ConcurrencyLimit(RuntimeError):
    """并发额度已满（由 API 层翻译为 429）。"""


class SessionBusy(RuntimeError):
    """同一个诊断会话正在推进（由 API 层翻译为 409）。"""


@contextmanager
def reasoning_slot():
    """占用一个推理名额；拿不到就立即失败，不把请求挂在队列里等超时。"""
    if not _REASONING_SLOTS.acquire(blocking=False):
        raise ConcurrencyLimit(
            f"当前同时推理数已达上限（{REASONING_LIMIT}），请稍后重试。")
    try:
        yield
    finally:
        _REASONING_SLOTS.release()


def write_guard(fn):
    """给会写库的 HTTP 入口加写事务锁（保留原函数签名供 FastAPI 使用）。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with WRITE_LOCK:
            return fn(*args, **kwargs)
    return wrapper