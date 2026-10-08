import base64
import binascii
import hashlib
import re
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from app.modules.chat.errors import ChatError
from app.modules.chat.formatting import markdown_para_texto_simples


class SessionCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1] = 1
    owner: str
    updated_at: AwareDatetime
    session_id: UUID


def encode_session_cursor(updated_at: datetime, session_id: UUID, uid: str) -> str:
    payload = SessionCursor(
        owner=hashlib.sha256(uid.encode()).hexdigest(),
        updated_at=updated_at, session_id=session_id,
    ).model_dump_json()
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_session_cursor(cursor: str | None, uid: str) -> tuple[datetime, str] | None:
    if cursor is None:
        return None
    try:
        if len(cursor) > 1024 or not re.fullmatch(r"[A-Za-z0-9_-]+", cursor):
            raise ValueError
        payload = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        decoded = SessionCursor.model_validate_json(payload)
        if decoded.owner != hashlib.sha256(uid.encode()).hexdigest():
            raise ValueError
        return decoded.updated_at.astimezone(timezone.utc), str(decoded.session_id)
    except (ValueError, ValidationError, binascii.Error):
        raise ChatError(400, "Cursor invalido para esta consulta.") from None


def compact_session_text(value: str, max_length: int) -> str:
    # A prévia exibe apenas o rótulo dos links, inclusive ações da interface.
    value = re.sub(r"!?\[([^\]]*)\]\([^\n]*?\)", r"\1", value)
    value = re.sub(r"(?m)^\s*\[[^\]]+\]:\s+\S+.*$", "", value)
    value = re.sub(r"\[([^\]]+)\]\[[^\]]*\]", r"\1", value)
    text = " ".join(markdown_para_texto_simples(value).split())
    return text if len(text) <= max_length else text[:max_length - 1].rstrip() + "…"


def session_title(doc: dict, new_question: str = "") -> str:
    title = doc.get("titulo")
    if isinstance(title, str) and title.strip():
        return compact_session_text(title, 80)
    first = doc.get("primeira_pergunta") or next((
        message for message in doc.get("mensagens", [])
        if message.get("role") == "human" and isinstance(message.get("content"), str)
    ), {})
    return compact_session_text(first.get("content") or new_question, 80) or "Nova conversa"


def session_preview(doc: dict) -> str:
    preview = doc.get("ultima_mensagem_previa")
    if not isinstance(preview, str):
        last = doc.get("ultima_mensagem") or next((
            message for message in reversed(doc.get("mensagens", []))
            if message.get("role") in {"human", "assistant"} and isinstance(message.get("content"), str)
        ), {})
        preview = last.get("content", "")
    return compact_session_text(preview, 200)
