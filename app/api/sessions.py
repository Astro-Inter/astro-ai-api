from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response

from app.api.auth import CurrentUserDependency
from app.modules.chat.errors import ChatError
from app.modules.chat.schemas import SessionResponse


router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("/{session_id}/iniciar", response_model=SessionResponse)
async def start_session(session_id: UUID, user: CurrentUserDependency,
                        request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        return await request.app.state.chat_service.start(session_id, user)
    except ChatError as error:
        raise HTTPException(error.status_code, error.detail) from None


@router.post("/{session_id}/encerrar", response_model=SessionResponse)
async def end_session(session_id: UUID, user: CurrentUserDependency,
                      request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        return await request.app.state.chat_service.end(session_id, user)
    except ChatError as error:
        raise HTTPException(error.status_code, error.detail) from None
