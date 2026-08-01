from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "BORIS SaaS"
    debug: bool = True
    secret_key: str = "change-me-in-production"
    database_url: str = "postgresql://boris:boris_secret@localhost:5432/boris"
    cors_origins: list[str] = ["http://localhost:3000"]


settings = Settings()
