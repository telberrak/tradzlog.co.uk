import io
import json
from urllib.error import HTTPError

from scripts import smoke_check
from scripts.smoke_check import SmokeStatus, check_endpoint, run_smoke_checks


class FakeResponse:
    def __init__(self, status: int, payload: dict[str, object]) -> None:
        self.status = status
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_check_endpoint_passes_for_ok_payload(monkeypatch) -> None:
    calls: list[str] = []

    def fake_urlopen(request, timeout: float):
        calls.append(request.full_url)
        assert timeout == 2.0
        return FakeResponse(200, {"status": "ok"})

    monkeypatch.setattr(smoke_check, "urlopen", fake_urlopen)

    result = check_endpoint("https://api.example.com", "/livez", timeout=2.0)

    assert result.status == SmokeStatus.PASS
    assert calls == ["https://api.example.com/livez"]


def test_check_endpoint_fails_for_non_ok_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        smoke_check,
        "urlopen",
        lambda request, timeout: FakeResponse(200, {"status": "degraded"}),
    )

    result = check_endpoint("https://api.example.com", "/readyz")

    assert result.status == SmokeStatus.FAIL
    assert "Unexpected status payload" in result.message


def test_check_endpoint_fails_for_http_errors(monkeypatch) -> None:
    def fake_urlopen(request, timeout: float):
        raise HTTPError(request.full_url, 503, "Service Unavailable", {}, io.BytesIO())

    monkeypatch.setattr(smoke_check, "urlopen", fake_urlopen)

    result = check_endpoint("https://api.example.com", "/readyz")

    assert result.status == SmokeStatus.FAIL
    assert result.message == "HTTP 503"


def test_run_smoke_checks_checks_all_health_endpoints(monkeypatch) -> None:
    checked: list[str] = []

    def fake_check_endpoint(base_url: str, endpoint: str, timeout: float):
        checked.append(endpoint)
        return smoke_check.SmokeResult(endpoint, SmokeStatus.PASS, base_url)

    monkeypatch.setattr(smoke_check, "check_endpoint", fake_check_endpoint)

    results = run_smoke_checks("https://api.example.com")

    assert [result.endpoint for result in results] == ["/livez", "/readyz", "/healthz"]
    assert checked == ["/livez", "/readyz", "/healthz"]
