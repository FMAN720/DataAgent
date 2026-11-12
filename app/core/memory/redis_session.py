"""Redis 会话窗口；一条 List 元素是一整轮，写入、裁剪和续期使用同一事务。"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from loguru import logger

from app.core.memory.session import InMemorySessionStore, SessionStore

if TYPE_CHECKING:
    from app.config import Settings


class SessionStoreUnavailable(RuntimeError):
    """安全的 API 错误，不携带 Redis URL、口令或对话内容。"""

    def __init__(self, *, writing: bool = False) -> None:
        detail = "会话存储暂时不可用，请检查 Redis 服务"
        if writing:
            detail += "；本轮历史未能确认保存，重试可能再次调用模型"
        super().__init__(detail)


class RedisSessionStore:
    backend = "redis"

    def __init__(
        self,
        client: Any,
        *,
        max_turns: int = 10,
        ttl_seconds: int = 3600,
        key_prefix: str = "analyst:session:v1:",
    ) -> None:
        if max_turns < 1 or ttl_seconds < 1 or not key_prefix.strip():
            raise ValueError("会话轮数、TTL 和键前缀必须有效")
        self._client = client
        self.max_turns = max_turns
        self.ttl_seconds = ttl_seconds
        self.key_prefix = key_prefix

    @classmethod
    def from_settings(cls, settings: Settings) -> RedisSessionStore:
        # 只在选用 Redis 时导入；最小 SQLite 版本无需安装该可选依赖。
        try:
            from redis.asyncio import Redis
            from redis.asyncio.retry import Retry
            from redis.backoff import NoBackoff
        except ImportError:
            raise RuntimeError("Redis 会话需要安装 requirements-redis.txt 中的依赖") from None
        try:
            client = Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                protocol=2,
                socket_connect_timeout=settings.redis_timeout_seconds,
                socket_timeout=settings.redis_timeout_seconds,
                max_connections=settings.redis_max_connections,
                retry=Retry(NoBackoff(), 0),
                retry_on_timeout=False,
            )
        except (ValueError, TypeError):
            raise ValueError("REDIS_URL 配置无效，请使用 redis:// 或 rediss:// 连接地址") from None
        return cls(
            client,
            max_turns=settings.session_max_turns,
            ttl_seconds=settings.session_ttl_seconds,
            key_prefix=settings.redis_key_prefix,
        )

    def _key(self, session_id: str) -> str:
        # 不将外部传入的 ID 当作键路径，也不在 Redis 键名中暴露原始 ID。
        return self.key_prefix + hashlib.sha256(session_id.encode("utf-8")).hexdigest()

    async def check_ready(self) -> None:
        try:
            if not await self._client.ping():
                raise OSError("ping failed")
        except Exception as exc:
            logger.warning("Redis 会话探测失败: {}", type(exc).__name__)
            raise SessionStoreUnavailable() from None

    async def get_history(self, session_id: str) -> list[dict[str, Any]]:
        try:
            raw_turns = await self._client.lrange(self._key(session_id), -self.max_turns, -1)
            messages = []
            for raw in raw_turns:
                turn = json.loads(raw)
                if (
                    not isinstance(turn, dict)
                    or turn.get("v") != 1
                    or not isinstance(turn.get("user"), str)
                    or not isinstance(turn.get("assistant"), str)
                ):
                    raise ValueError("invalid session payload")
                messages.extend(
                    [
                        {"role": "user", "content": turn["user"]},
                        {"role": "assistant", "content": turn["assistant"]},
                    ]
                )
            return messages
        except Exception as exc:
            logger.warning("Redis 会话读取失败: {}", type(exc).__name__)
            raise SessionStoreUnavailable() from None

    async def append_turn(self, session_id: str, user: str, assistant: str) -> None:
        payload = json.dumps({"v": 1, "user": user, "assistant": assistant}, ensure_ascii=False)
        key = self._key(session_id)
        try:
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.rpush(key, payload)
                pipe.ltrim(key, -self.max_turns, -1)
                pipe.expire(key, self.ttl_seconds)
                await pipe.execute()
        except Exception as exc:
            # 不自动重试：EXEC 成功但响应丢失时，重试可能重复插入同一轮。
            logger.warning("Redis 会话写入失败: {}", type(exc).__name__)
            raise SessionStoreUnavailable(writing=True) from None

    async def clear(self, session_id: str) -> None:
        try:
            await self._client.delete(self._key(session_id))
        except Exception as exc:
            logger.warning("Redis 会话清除失败: {}", type(exc).__name__)
            raise SessionStoreUnavailable() from None

    async def aclose(self) -> None:
        await self._client.aclose()


def build_session_store(settings: Settings) -> SessionStore:
    if settings.session_backend == "redis":
        return RedisSessionStore.from_settings(settings)
    return InMemorySessionStore(
        max_turns=settings.session_max_turns,
        max_sessions=settings.session_max_sessions,
        ttl_seconds=settings.session_ttl_seconds,
    )
