from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

try:
    import sentry_sdk
    from sentry_sdk.integrations.logging import LoggingIntegration
except ImportError:  # pragma: no cover - production installs include sentry-sdk
    sentry_sdk = None  # type: ignore[assignment]
    LoggingIntegration = None  # type: ignore[assignment]

from tradzlog_api.config import settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        for key in ("method", "path", "status_code", "duration_ms", "client"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging() -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())


def init_sentry() -> None:
    if not settings.sentry_dsn or sentry_sdk is None or LoggingIntegration is None:
        return
    sentry_logging = LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        traces_sample_rate=0.05,
        integrations=[sentry_logging],
    )


def init_observability() -> None:
    configure_logging()
    init_sentry()


def start_request(request_id: str | None = None) -> tuple[str, float]:
    assigned_request_id = request_id or str(uuid4())
    request_id_var.set(assigned_request_id)
    return assigned_request_id, perf_counter()


def finish_request(
    *,
    logger: logging.Logger,
    method: str,
    path: str,
    status_code: int,
    started_at: float,
    client: str,
) -> None:
    duration_ms = round((perf_counter() - started_at) * 1000, 2)
    logger.info(
        "request completed",
        extra={
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "client": client,
        },
    )
