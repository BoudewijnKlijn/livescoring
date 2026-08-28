"""Configuratie uit environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Instellingen van de app.

    Volgorde: echte environment variables winnen altijd, daarna `.env.local`, daarna `.env`.
    Lokaal werken doe je dus met `.env.local`; op Render staan de waarden in het dashboard en
    bestaat geen van beide bestanden.
    """

    database_url: str = "postgresql+psycopg://livescoring:livescoring@localhost:5434/livescoring"
    secret_key: str = "dev-secret-niet-gebruiken-in-productie"
    admin_password: str = "admin"
    base_url: str = "http://localhost:8000"
    cookie_secure: bool = False

    # Bevestigingsmail na het tekenen. Leeg = uit, en dan wordt er niets verstuurd.
    brevo_api_key: str = ""
    mail_from: str = ""
    mail_from_name: str = "Wedstrijdleiding"

    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")


settings = Settings()
