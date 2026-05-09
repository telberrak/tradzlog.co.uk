from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from redis import Redis
from rq import Queue

from tradzlog_api.config import settings

QUEUE_DEFAULT = "default"
QUEUE_ANALYTICS = "analytics"
QUEUE_IMPORTS = "imports"
QUEUE_REPORTS = "reports"
QUEUE_AI = "ai"


@dataclass(frozen=True)
class EnqueuedJob:
    queue: str
    job_id: str
    function_name: str


def redis_connection() -> Redis:
    return Redis.from_url(settings.redis_url)


def queue(name: str) -> Queue:
    return Queue(name, connection=redis_connection())


def enqueue_job(queue_name: str, func: Callable[..., Any], *args: Any, **kwargs: Any) -> EnqueuedJob:
    job = queue(queue_name).enqueue(func, *args, **kwargs)
    return EnqueuedJob(queue=queue_name, job_id=job.id, function_name=f"{func.__module__}.{func.__name__}")
