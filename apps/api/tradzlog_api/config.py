from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_JWT_SECRETS = {"", "dev-only-change-me", "replace-with-a-long-random-secret"}


class Settings(BaseSettings):
    app_env: str = "local"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "dev-only-change-me"
    jwt_issuer: str = "tradzlog.local"
    access_token_expire_minutes: int = 43200
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5-5"
    sentry_dsn: str | None = None
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8000", "http://localhost:8001"]
    # Screenshot storage: "local" (var/uploads) or "s3" (private bucket, presigned links).
    storage_backend: str = "local"
    s3_bucket: str | None = None
    s3_region: str | None = None
    s3_prefix: str = ""
    s3_endpoint_url: str | None = None  # only for S3-compatible servers (MinIO in tests); unset on AWS
    s3_url_ttl_seconds: int = 3600
    # When closed, sign-up needs REGISTRATION_INVITE_CODE (private beta). Production defaults to closed.
    registration_open: bool = True
    registration_invite_code: str | None = None
    # Unfinished features stay hidden (404, no links) until they are real: community/mentor pages and
    # public trade shares, and the simulated billing page that M2 replaces with Stripe.
    feature_community: bool = False
    feature_billing: bool = False
    # Shown on the legal and error pages; must be a mailbox someone reads.
    support_email: str = "tarik.elberrak@datakratos.com"
    # Transactional email (tradzlog_api.services.email): "log" sends nothing, "ses" uses Amazon SES.
    email_backend: str = "log"
    email_from: str = "TradzLog <no-reply@tradzlog.com>"
    ses_region: str = "eu-west-2"
    # Where links in emails point.
    public_url: str = "http://localhost:8001"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def require_real_secret_outside_local(self) -> "Settings":
        if self.app_env not in {"local", "test"} and (
            self.jwt_secret in INSECURE_JWT_SECRETS or len(self.jwt_secret) < 32
        ):
            raise ValueError("JWT_SECRET must be a unique value of at least 32 characters outside local")
        return self


settings = Settings()
