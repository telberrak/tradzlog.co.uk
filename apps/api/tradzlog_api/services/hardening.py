from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import time
from uuid import uuid4

from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session

from tradzlog_api.config import settings

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    reset_at: datetime


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, list[datetime]] = {}

    def check(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = datetime.now(UTC)
        window_start = now - timedelta(seconds=window_seconds)
        timestamps = [stamp for stamp in self._buckets.get(key, []) if stamp > window_start]
        allowed = len(timestamps) < limit
        if allowed:
            timestamps.append(now)
        self._buckets[key] = timestamps
        oldest = timestamps[0] if timestamps else now
        reset_at = oldest + timedelta(seconds=window_seconds)
        remaining = max(limit - len(timestamps), 0)
        return RateLimitResult(allowed=allowed, remaining=remaining, reset_at=reset_at)


class RedisBackedRateLimiter:
    def __init__(self, redis: Redis, fallback: InMemoryRateLimiter | None = None) -> None:
        self.redis = redis
        self.fallback = fallback or InMemoryRateLimiter()

    def check(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = time()
        redis_key = f"rate-limit:{key}"
        window_start = now - window_seconds
        try:
            pipe = self.redis.pipeline()
            pipe.zremrangebyscore(redis_key, 0, window_start)
            pipe.zcard(redis_key)
            _, current_count = pipe.execute()
            current = int(current_count)
            allowed = current < limit
            if allowed:
                self.redis.zadd(redis_key, {f"{now}:{uuid4()}": now})
                current += 1
            self.redis.expire(redis_key, window_seconds)
            oldest = self.redis.zrange(redis_key, 0, 0, withscores=True)
            if oldest:
                reset_timestamp = float(oldest[0][1]) + window_seconds
            else:
                reset_timestamp = now + window_seconds
            return RateLimitResult(
                allowed=allowed,
                remaining=max(limit - current, 0),
                reset_at=datetime.fromtimestamp(reset_timestamp, UTC),
            )
        except Exception:
            return self.fallback.check(key, limit, window_seconds)


def build_rate_limiter() -> RedisBackedRateLimiter:
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=0.25, socket_timeout=0.25)
    return RedisBackedRateLimiter(redis)


rate_limiter = build_rate_limiter()


def check_database(session: Session) -> str:
    session.execute(text("select 1"))
    return "ok"


def check_redis() -> str:
    try:
        Redis.from_url(settings.redis_url, socket_connect_timeout=0.25, socket_timeout=0.25).ping()
    except Exception:
        return "unavailable"
    return "ok"
