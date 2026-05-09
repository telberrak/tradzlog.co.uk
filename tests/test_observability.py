import json
import logging

from tradzlog_api.services.observability import JsonFormatter, request_id_var


def test_json_formatter_includes_request_context() -> None:
    request_id_var.set("req-123")
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="tradzlog.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="request completed",
        args=(),
        exc_info=None,
    )
    record.method = "POST"
    record.path = "/api/auth/login"
    record.status_code = 200
    record.duration_ms = 12.5
    record.client = "127.0.0.1"

    payload = json.loads(formatter.format(record))

    assert payload["level"] == "INFO"
    assert payload["request_id"] == "req-123"
    assert payload["method"] == "POST"
    assert payload["path"] == "/api/auth/login"
    assert payload["status_code"] == 200
