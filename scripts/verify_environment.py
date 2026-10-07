from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from enum import Enum

from redis import Redis
from sqlalchemy import create_engine, text


class CheckStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    message: str


REQUIRED_ENV = ("DATABASE_URL", "REDIS_URL", "JWT_SECRET")
OPTIONAL_ENV = (
    "ANTHROPIC_API_KEY",
    "RESEND_API_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "R2_BUCKET",
    "SENTRY_DSN",
    "AXIOM_TOKEN",
)


def check_required_env() -> CheckResult:
    missing = [key for key in REQUIRED_ENV if not os.getenv(key)]
    if missing:
        return CheckResult("required-env", CheckStatus.FAIL, f"Missing required variables: {', '.join(missing)}")
    return CheckResult("required-env", CheckStatus.PASS, "Required environment variables are set")


def check_optional_env() -> CheckResult:
    missing = [key for key in OPTIONAL_ENV if not os.getenv(key)]
    if missing:
        return CheckResult("optional-env", CheckStatus.WARN, f"Optional integrations not configured: {', '.join(missing)}")
    return CheckResult("optional-env", CheckStatus.PASS, "Optional integration variables are set")


def check_jwt_secret() -> CheckResult:
    secret = os.getenv("JWT_SECRET", "")
    if secret in {"", "dev-only-change-me", "replace-with-a-long-random-secret"} or len(secret) < 32:
        return CheckResult("jwt-secret", CheckStatus.FAIL, "JWT_SECRET must be unique and at least 32 characters")
    return CheckResult("jwt-secret", CheckStatus.PASS, "JWT_SECRET length and value look production-ready")


def check_imports() -> CheckResult:
    try:
        import tradzlog_api.main  # noqa: F401
        import tradzlog_db.models  # noqa: F401
        import tradzlog_web.main  # noqa: F401
    except Exception as exc:
        return CheckResult("imports", CheckStatus.FAIL, f"Application imports failed: {exc}")
    return CheckResult("imports", CheckStatus.PASS, "Application modules import successfully")


def check_database() -> CheckResult:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return CheckResult("database", CheckStatus.FAIL, "DATABASE_URL is not set")
    try:
        engine = create_engine(database_url, pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except Exception as exc:
        return CheckResult("database", CheckStatus.FAIL, f"Database check failed: {exc}")
    return CheckResult("database", CheckStatus.PASS, "Database connection succeeded")


def check_redis() -> CheckResult:
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return CheckResult("redis", CheckStatus.FAIL, "REDIS_URL is not set")
    try:
        Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1).ping()
    except Exception as exc:
        return CheckResult("redis", CheckStatus.FAIL, f"Redis check failed: {exc}")
    return CheckResult("redis", CheckStatus.PASS, "Redis connection succeeded")


def run_checks(skip_network: bool = False) -> list[CheckResult]:
    checks = [check_required_env(), check_optional_env(), check_jwt_secret(), check_imports()]
    if not skip_network:
        checks.extend([check_database(), check_redis()])
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify TradzLog deployment environment.")
    parser.add_argument("--skip-network", action="store_true", help="Skip database and Redis checks")
    args = parser.parse_args()
    results = run_checks(skip_network=args.skip_network)
    for result in results:
        print(f"[{result.status.value}] {result.name}: {result.message}")
    return 1 if any(result.status == CheckStatus.FAIL for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
