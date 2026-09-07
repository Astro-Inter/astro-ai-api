from pydantic import BaseModel


class CurrentUser(BaseModel):
    uid: str
    role: str | None = None
