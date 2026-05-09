from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "dev-only-change-me"
    jwt_issuer: str = "tradzlog.local"
    access_token_expire_minutes: int = 43200
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-20250514"
    sentry_dsn: str | None = None
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
