"""
会话记忆：保存最近 N 轮对话，支持多轮追问（「那华北呢？」）。

只保存每轮的 user 问题和最终答案，不保存中间的 tool 消息：
工具输出体积大、对后续轮次价值低，而且答案里已经包含了结论。
默认进程内存储；多实例部署使用 redis_session.RedisSessionStore。
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any, Protocol


class SessionStore(Protocol):
    backend: str

    async def check_ready(self) -> None: ...

    async def aclose(self) -> None: ...

    async def get_history(self, session_id: str) -> list[dict[str, Any]]: ...

    async def append_turn(self, session_id: str, user: str, assistant: str) -> None: ...

    async def clear(self, session_id: str) -> None: ...


class InMemorySessionStore:
    """带容量上限和过期时间的进程内会话存储。"""

    backend = "memory"

    async def check_ready(self) -> None:
        pass

    async def aclose(self) -> None:
        pass

    def __init__(
        self, max_turns: int = 10, max_sessions: int = 1000, ttl_seconds: float = 3600
    ) -> None:
        self.max_turns = max(1, max_turns)
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds
        self._data: OrderedDict[str, tuple[float, list[dict[str, Any]]]] = OrderedDict()
        self._lock = asyncio.Lock()

    def _evict(self) -> None:
        now = time.monotonic()
        for sid in [s for s, (ts, _) in self._data.items() if now - ts > self.ttl_seconds]:
            del self._data[sid]
        while len(self._data) > self.max_sessions:
            self._data.popitem(last=False)

    async def get_history(self, session_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            self._evict()
            item = self._data.get(session_id)
            return list(item[1]) if item else []

    async def append_turn(self, session_id: str, user: str, assistant: str) -> None:
        async with self._lock:
            self._evict()
            _, msgs = self._data.pop(session_id, (0.0, []))
            msgs = msgs + [
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant},
            ]
            self._data[session_id] = (time.monotonic(), msgs[-self.max_turns * 2 :])
            self._evict()

    async def clear(self, session_id: str) -> None:
        async with self._lock:
            self._data.pop(session_id, None)
