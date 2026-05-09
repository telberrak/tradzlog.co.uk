from scripts.verify_environment import CheckStatus, check_jwt_secret, check_required_env, run_checks


def test_required_env_reports_missing_values(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    result = check_required_env()
    assert result.status == CheckStatus.FAIL
    assert "DATABASE_URL" in result.message


def test_jwt_secret_rejects_default_value(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "dev-only-change-me")
    result = check_jwt_secret()
    assert result.status == CheckStatus.FAIL


def test_skip_network_runs_without_database_or_redis(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    results = run_checks(skip_network=True)
    names = {result.name for result in results}
    assert "database" not in names
    assert "redis" not in names
