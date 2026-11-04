"""Configuração da aplicação, lida do ambiente e do arquivo .env."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.db.connection import DEFAULT_DB_PATH
from app.tools.complaints import DEFAULT_STATE_DB_PATH


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Chaves do servidor, usadas quando a requisição não traz a própria (BYOK).
    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None

    llm_provider: str = "anthropic"
    llm_model: str | None = None  # só vale para llm_provider; o padrão fica em app/llm/factory.py

    catalog_db_path: Path = DEFAULT_DB_PATH
    state_db_path: Path = DEFAULT_STATE_DB_PATH

    session_ttl_seconds: int = Field(default=3600, gt=0)
    max_sessions: int = Field(default=10_000, gt=0)

    def server_key(self, provider: str) -> str | None:
        key: SecretStr | None = getattr(self, f"{provider}_api_key")
        return key.get_secret_value() if key else None
