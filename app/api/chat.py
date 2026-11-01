import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.api.auth import CurrentUserDependency
from app.infrastructure.astro_queries import request_token
from app.modules.chat.errors import ChatError, InvalidAgentResponse
from app.modules.chat.schemas import ChatRequest, ChatResponse
from app.api.chat_contracts import SESSION_ERRORS, session_error


router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)


@router.post("/messages", response_model=ChatResponse, summary="Enviar mensagem ao chatbot",
             responses={**SESSION_ERRORS, 404: session_error("Conversa nao encontrada."),
                        409: session_error("Sessão ocupada, encerrada/em encerramento ou limite de histórico atingido.",
                                           "Conversa encerrada ou em encerramento. Use /iniciar para retomar uma conversa encerrada; solicite recuperação ao suporte se houver encerramento legado pendente."),
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
    credentials = request.headers.get("authorization", "")
    token_handle = request_token.set(credentials[7:] if credentials.lower().startswith("bearer ") else None)
    try:
        return await request.app.state.chat_service.chat(body, user, markdown=markdown)
    except ChatError as error:
        from app.diagnostics import emit
        stages={"guardrail_entrada","roteador","rh","sst","agenda","faq","orquestrador","juiz","guardrail_saida","resumo","embeddings"}
        reasons={"provider_configuration","insufficient_credits","model_unavailable","rate_limited","provider_failure","provider_response","review_unavailable"}
        emit("chat_request_rejected",level="WARNING",sink=logger,
             error_stage=error.stage if error.stage in stages else "chat",
             error_reason=error.reason if error.reason in reasons else "unspecified",
             http_status=error.status_code)
        if isinstance(error, InvalidAgentResponse):
            logger.warning("Resposta invalida no estagio do chat: %s", error.stage)
        headers = {"Retry-After": str(error.retry_after)} if error.retry_after else None
        raise HTTPException(error.status_code, detail=error.detail, headers=headers) from None

    finally:
        request_token.reset(token_handle)
