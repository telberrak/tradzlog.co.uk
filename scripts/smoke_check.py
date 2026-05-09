from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from enum import StrEnum
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


class SmokeStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True)
class SmokeResult:
    endpoint: str
    status: SmokeStatus
    message: str


HEALTH_ENDPOINTS = ("/livez", "/readyz", "/healthz")


def check_endpoint(base_url: str, endpoint: str, timeout: float = 5.0) -> SmokeResult:
    url = urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            payload = json.loads(body) if body else {}
            if response.status >= 400:
                return SmokeResult(endpoint, SmokeStatus.FAIL, f"HTTP {response.status}")
            if payload.get("status") != "ok":
                return SmokeResult(
                    endpoint,
                    SmokeStatus.FAIL,
                    f"Unexpected status payload: {payload!r}",
                )
    except HTTPError as exc:
        return SmokeResult(endpoint, SmokeStatus.FAIL, f"HTTP {exc.code}")
    except (OSError, URLError, json.JSONDecodeError) as exc:
        return SmokeResult(endpoint, SmokeStatus.FAIL, f"Request failed: {exc}")
    return SmokeResult(endpoint, SmokeStatus.PASS, "Endpoint is healthy")


def run_smoke_checks(
    base_url: str,
    endpoints: tuple[str, ...] = HEALTH_ENDPOINTS,
    timeout: float = 5.0,
) -> list[SmokeResult]:
    return [check_endpoint(base_url, endpoint, timeout=timeout) for endpoint in endpoints]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run TradzLog deployment smoke checks.")
    parser.add_argument("base_url", help="Base URL for the running API, e.g. https://api.example.com")
    parser.add_argument("--timeout", type=float, default=5.0, help="Request timeout in seconds")
    args = parser.parse_args()

    results = run_smoke_checks(args.base_url, timeout=args.timeout)
    for result in results:
        print(f"[{result.status.value}] {result.endpoint}: {result.message}")
    return 1 if any(result.status == SmokeStatus.FAIL for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
