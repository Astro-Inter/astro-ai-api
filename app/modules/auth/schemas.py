from typing import Literal

from pydantic import BaseModel, Field, SecretStr


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=4096)


class LoginResponse(BaseModel):
    access_token: str = Field(min_length=1)
    expires_in: int = Field(gt=0)
    token_type: Literal["Bearer"] = "Bearer"
