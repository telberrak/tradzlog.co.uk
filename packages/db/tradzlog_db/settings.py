from pydantic_settings import BaseSettings, SettingsConfigDict


class DbSettings(BaseSettings):
    database_url: str = "postgresql+psycopg://tradzlog:tradzlog@localhost:5432/tradzlog"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


db_settings = DbSettings()
