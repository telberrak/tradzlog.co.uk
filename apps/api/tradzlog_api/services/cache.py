from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from redis import Redis

from tradzlog_api.config import settings


class JsonCache:
    def __init__(self) -> None:
        self._memory: dict[str, tuple[datetime, str]] = {}
        self._redis: Redis | None = None

    def _client(self) -> Redis | None:
        if self._redis is not None:
            return self._redis
        try:
            self._redis = Redis.from_url(settings.redis_url, socket_connect_timeout=0.25, socket_timeout=0.25)
            self._redis.ping()
        except Exception:
            self._redis = None
        return self._redis

    def get(self, key: str) -> Any | None:
        client = self._client()
        if client is not None:
            raw = client.get(key)
            return json.loads(raw) if raw else None
        cached = self._memory.get(key)
        if cached is None:
            return None
        expires_at, raw = cached
        if expires_at <= datetime.now(UTC):
            self._memory.pop(key, None)
            return None
        return json.loads(raw)

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        raw = json.dumps(value, default=str)
        client = self._client()
        if client is not None:
            client.setex(key, ttl_seconds, raw)
            return
        self._memory[key] = (datetime.now(UTC) + timedelta(seconds=ttl_seconds), raw)

    def delete_prefix(self, prefix: str) -> None:
        client = self._client()
        if client is not None:
            for key in client.scan_iter(f"{prefix}*"):
                client.delete(key)
            return
        for key in list(self._memory):
            if key.startswith(prefix):
                self._memory.pop(key, None)


analytics_cache = JsonCache()


def decimalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: decimalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decimalize(item) for item in value]
    if isinstance(value, str):
        try:
            return Decimal(value)
        except Exception:
            return value
    return value
