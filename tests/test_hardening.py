from tradzlog_api.services.hardening import (
    SECURITY_HEADERS,
    InMemoryRateLimiter,
    RedisBackedRateLimiter,
)


def test_rate_limiter_blocks_after_limit() -> None:
    limiter = InMemoryRateLimiter()
    assert limiter.check("login:127.0.0.1", limit=2, window_seconds=60).allowed
    assert limiter.check("login:127.0.0.1", limit=2, window_seconds=60).allowed
    blocked = limiter.check("login:127.0.0.1", limit=2, window_seconds=60)
    assert not blocked.allowed
    assert blocked.remaining == 0


def test_security_headers_include_clickjacking_and_content_type_protection() -> None:
    assert SECURITY_HEADERS["X-Frame-Options"] == "DENY"
    assert SECURITY_HEADERS["X-Content-Type-Options"] == "nosniff"


class FakePipeline:
    def __init__(self, redis: "FakeRedis") -> None:
        self.redis = redis
        self.commands: list[tuple[str, tuple[object, ...]]] = []

    def zremrangebyscore(self, *args: object) -> None:
        self.commands.append(("zremrangebyscore", args))

    def zcard(self, *args: object) -> None:
        self.commands.append(("zcard", args))

    def execute(self) -> list[int]:
        results: list[int] = []
        for command, args in self.commands:
            if command == "zremrangebyscore":
                self.redis.zremrangebyscore(*args)
                results.append(0)
            if command == "zcard":
                results.append(self.redis.zcard(*args))
        return results


class FakeRedis:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.members: dict[str, dict[str, float]] = {}

    def pipeline(self) -> FakePipeline:
        if self.fail:
            raise ConnectionError("redis unavailable")
        return FakePipeline(self)

    def zremrangebyscore(self, key: str, minimum: object, maximum: object) -> None:
        min_score = float(minimum)
        max_score = float(maximum)
        bucket = self.members.setdefault(key, {})
        for member, score in list(bucket.items()):
            if min_score <= score <= max_score:
                del bucket[member]

    def zcard(self, key: str) -> int:
        return len(self.members.setdefault(key, {}))

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.members.setdefault(key, {}).update(mapping)

    def expire(self, key: str, seconds: int) -> None:
        del key, seconds

    def zrange(
        self, key: str, start: int, end: int, withscores: bool = False
    ) -> list[tuple[str, float]]:
        del start, end
        ordered = sorted(self.members.setdefault(key, {}).items(), key=lambda item: item[1])
        return ordered[:1] if withscores else [(member, 0.0) for member, _ in ordered[:1]]


def test_redis_rate_limiter_blocks_after_shared_limit() -> None:
    limiter = RedisBackedRateLimiter(FakeRedis())  # type: ignore[arg-type]

    assert limiter.check("login:127.0.0.1", limit=2, window_seconds=60).allowed
    assert limiter.check("login:127.0.0.1", limit=2, window_seconds=60).allowed
    blocked = limiter.check("login:127.0.0.1", limit=2, window_seconds=60)

    assert not blocked.allowed
    assert blocked.remaining == 0


def test_redis_rate_limiter_falls_back_to_memory_when_redis_fails() -> None:
    limiter = RedisBackedRateLimiter(FakeRedis(fail=True))  # type: ignore[arg-type]

    assert limiter.check("login:127.0.0.1", limit=1, window_seconds=60).allowed
    blocked = limiter.check("login:127.0.0.1", limit=1, window_seconds=60)

    assert not blocked.allowed
