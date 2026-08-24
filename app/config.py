"""Configuratie uit environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Instellingen van de app. Alles komt uit de environment of uit .env."""

    database_url: str = "postgresql+psycopg://livescoring:livescoring@localhost:5434/livescoring"
    secret_key: str = "dev-secret-niet-gebruiken-in-productie"
    admin_password: str = "admin"
    base_url: str = "http://localhost:8000"
    cookie_secure: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
