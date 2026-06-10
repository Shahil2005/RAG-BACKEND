"""Redis service (port of the NestJS redis/redis.service.ts).

Best-effort cache + connectivity layer used by the RAG cache and OAuth state
store. Every operation is timeout-guarded and never throws: when Redis is
unavailable the caller silently degrades (cache miss / in-memory fallback),
exactly like the original NestJS service.

Builds on :class:`app.core.redis.RedisHelper` (a low-level wrapper kept intact)
and exposes the same public surface the NestJS ``RedisService`` had:
``get_client`` / ``ping`` / ``get_cached`` / ``set_cached`` plus thin
``get`` / ``set`` / ``delete`` passthroughs.
"""

import asyncio
import json
from typing import Any
from urllib.parse import urlparse

from redis.asyncio import Redis

from app import logger
from app.core.settings import settings

REDIS_OP_TIMEOUT_S = 2.0


class RedisService:
    """Timeout-guarded Redis client mirroring the NestJS RedisService."""

    def __init__(self) -> None:
        self._client: Redis | None = None

    def get_client(self) -> Redis:
        """Lazily build a non-blocking Redis client (does not connect yet).

        Mirrors ``getClient()``: ``REDIS_URL`` (default redis://localhost:6379),
        one retry per request, short connect timeout, offline queue disabled so
        operations fail fast instead of hanging when Redis is down.
        """
        if self._client is None:
            url = settings.redis_url or "redis://localhost:6379"
            parsed = urlparse(url)
            self._client = Redis(
                host=parsed.hostname or "localhost",
                port=parsed.port or settings.redis_port,
                password=parsed.password or settings.redis_password,
                db=int(parsed.path.lstrip("/")) if parsed.path.strip("/") else 0,
                ssl=parsed.scheme == "rediss",
                decode_responses=True,
                socket_connect_timeout=REDIS_OP_TIMEOUT_S,
                socket_timeout=REDIS_OP_TIMEOUT_S,
                retry_on_timeout=False,
                single_connection_client=False,
            )
        return self._client

    async def ping(self) -> bool:
        """Best-effort connectivity check (does not throw)."""
        try:
            client = self.get_client()
            result = await asyncio.wait_for(client.ping(), REDIS_OP_TIMEOUT_S)
            return result is True or result == "PONG"
        except Exception:
            return False

    def _log_cache_skip(self, operation: str, key: str, err: object) -> None:
        message = str(err) if isinstance(err, BaseException) else str(err)
        logger.warning(
            f"[RedisService] cache unavailable, skipping {operation} key={key}: {message}"
        )

    async def _safe_get(self, key: str) -> str | None:
        try:
            client = self.get_client()
            return await asyncio.wait_for(client.get(key), REDIS_OP_TIMEOUT_S)
        except Exception as err:
            self._log_cache_skip("get", key, err)
            return None

    async def _safe_set(self, key: str, value: str, ttl_seconds: int) -> None:
        try:
            client = self.get_client()
            await asyncio.wait_for(client.set(key, value, ex=ttl_seconds), REDIS_OP_TIMEOUT_S)
        except Exception as err:
            self._log_cache_skip("set", key, err)

    async def get_cached(self, key: str) -> Any | None:
        """Return a JSON-decoded cached value, or ``None`` on miss/parse error."""
        raw = await self._safe_get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            logger.warning(f"[RedisService] cache parse failed for key={key}")
            return None

    async def set_cached(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        """JSON-encode and store ``value`` with a TTL (default 300s)."""
        await self._safe_set(key, json.dumps(value, default=str), ttl_seconds)

    # --- thin passthroughs (get/set/del/etc) -----------------------------

    async def get(self, key: str) -> str | None:
        """Raw string GET (no JSON decode); ``None`` if missing/unavailable."""
        return await self._safe_get(key)

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        """Raw string SET with optional TTL; never throws."""
        try:
            client = self.get_client()
            await asyncio.wait_for(
                client.set(key, value, ex=ttl_seconds), REDIS_OP_TIMEOUT_S
            )
        except Exception as err:
            self._log_cache_skip("set", key, err)

    async def delete(self, key: str) -> bool:
        """DEL a key; ``False`` if missing or Redis unavailable."""
        try:
            client = self.get_client()
            return bool(await asyncio.wait_for(client.delete(key), REDIS_OP_TIMEOUT_S))
        except Exception as err:
            self._log_cache_skip("get", key, err)
            return False

    # NestJS alias (del is reserved in Python).
    async def del_(self, key: str) -> bool:
        return await self.delete(key)

    async def close(self) -> None:
        """Disconnect the client (port of onModuleDestroy)."""
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
