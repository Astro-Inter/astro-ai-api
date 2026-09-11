from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


AccessRole = Literal["ADMIN", "GESTOR", "GESTOR_WORKSPACE", "FUNCIONARIO"]


class CurrentUser(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str = Field(min_length=1, max_length=128)
    role: AccessRole
