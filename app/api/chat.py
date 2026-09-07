from fastapi import APIRouter, HTTPException, Request, Response

from app.api.auth import CurrentUserDependency
from app.modules.chat.errors import ChatError
from app.modules.chat.schemas import ChatRequest, ChatResponse


router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/messages", response_model=ChatResponse)
async def chat_message(
    body: ChatRequest, user: CurrentUserDependency, request: Request, response: Response,
) -> ChatResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await request.app.state.chat_service.chat(body, user)
    except ChatError as error:
        raise HTTPException(error.status_code, detail=error.detail) from None
