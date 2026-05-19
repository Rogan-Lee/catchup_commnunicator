from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

from app.core.logging import get_logger

log = get_logger(__name__)


class CacheLayer:
    """Thin async Redis JSON cache.

    All values are JSON-encoded. Missing keys return None. All operations
    swallow Redis errors and log them — cache is best-effort, never a hard
    dependency for request handling.
    """

    def __init__(self, redis: Redis, default_ttl: int = 3600):
        self.redis = redis
        self.default_ttl = default_ttl

    async def get(self, key: str) -> Any:
        try:
            raw = await self.redis.get(key)
        except Exception as e:
            log.warning("cache.get.failed", key=key, error=str(e))
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        try:
            await self.redis.set(key, payload, ex=ttl or self.default_ttl)
        except Exception as e:
            log.warning("cache.set.failed", key=key, error=str(e))

    async def delete(self, key: str) -> None:
        try:
            await self.redis.delete(key)
        except Exception as e:
            log.warning("cache.delete.failed", key=key, error=str(e))


def build_redis_client(redis_url: str) -> Redis:
    return Redis.from_url(redis_url, decode_responses=True)
