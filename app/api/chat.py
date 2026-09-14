import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.api.auth import CurrentUserDependency
from app.modules.chat.errors import ChatError, InvalidAgentResponse
from app.modules.chat.schemas import ChatRequest, ChatResponse


router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)


@router.post("/messages", response_model=ChatResponse)
async def chat_message(
    body: ChatRequest, user: CurrentUserDependency, request: Request, response: Response,
    markdown: Annotated[
        bool,
        Query(description="Retorna a resposta formatada em Markdown quando verdadeiro."),
    ] = True,
) -> ChatResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await request.app.state.chat_service.chat(body, user, markdown=markdown)
    except ChatError as error:
        if isinstance(error, InvalidAgentResponse):
            logger.warning("Resposta invalida no estagio do chat: %s", error.stage)
        raise HTTPException(error.status_code, detail=error.detail) from None
