import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.api.auth import CurrentUserDependency
from app.modules.chat.errors import ChatError, InvalidAgentResponse
from app.modules.chat.schemas import ChatRequest, ChatResponse
from app.api.chat_contracts import SESSION_ERRORS, session_error


router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)


@router.post("/messages", response_model=ChatResponse, summary="Enviar mensagem ao chatbot",
             responses={**SESSION_ERRORS, 404: session_error("Conversa nao encontrada."),
                        409: session_error("Sessão ocupada, encerrada/em encerramento ou limite de histórico atingido.",
                                           "Conversa encerrada ou em encerramento. Use /iniciar para retomar uma conversa encerrada; repita /encerrar se o encerramento estiver pendente."),
                        502: session_error("Resposta inválida dos agentes.")})
async def chat_message(
    body: ChatRequest, user: CurrentUserDependency, request: Request, response: Response,
    markdown: Annotated[
        bool,
        Query(description="Retorna a resposta formatada em Markdown quando verdadeiro."),
    ] = True,
) -> ChatResponse:
    """Sem session_id, cria uma conversa e retorna seu UUID. Com session_id,
    continua somente uma conversa ativa do próprio usuário. Sessões encerradas
    exigem POST /sessions/{session_id}/iniciar antes do envio (caso contrário 409).
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        return await request.app.state.chat_service.chat(body, user, markdown=markdown)
    except ChatError as error:
        if isinstance(error, InvalidAgentResponse):
            logger.warning("Resposta invalida no estagio do chat: %s", error.stage)
        headers = {"Retry-After": str(error.retry_after)} if error.retry_after else None
        raise HTTPException(error.status_code, detail=error.detail, headers=headers) from None
