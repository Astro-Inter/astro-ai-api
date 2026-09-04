from secrets import compare_digest

from pydantic import BaseModel

from app.core import config


class CurrentUser(BaseModel):
    uid: str
    role: str | None = None


def is_valid_dev_auth_token(provided_token: str | None) -> bool:
    dev_auth_enabled = (
        config.APP_ENV in {"development", "test"}
        and bool(config.DEV_AUTH_TOKEN)
    )
    if not provided_token or not dev_auth_enabled:
        return False

    return compare_digest(provided_token, config.DEV_AUTH_TOKEN)
