from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "staging", "production"] = "development"
    firebase_project_id: str = ""
    firebase_credentials_base64: SecretStr = SecretStr("")
    dev_auth_token: SecretStr = SecretStr("")

    @property
    def dev_auth_enabled(self) -> bool:
        return self.app_env in {"development", "test"} and bool(
            self.dev_auth_token.get_secret_value()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
