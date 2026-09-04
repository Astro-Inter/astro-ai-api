from secrets import compare_digest

from pydantic import BaseModel

from app.core.config import Settings


class CurrentUser(BaseModel):
    uid: str
    role: str | None = None


def is_valid_dev_auth_token(provided_token: str | None, settings: Settings) -> bool:
    if not provided_token or not settings.dev_auth_enabled:
        return False

    expected_token = settings.dev_auth_token.get_secret_value()
    return compare_digest(provided_token, expected_token)
