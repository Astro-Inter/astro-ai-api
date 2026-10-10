from uuid import UUID
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.api.auth import CurrentUserDependency
from app.api.chat_contracts import SESSION_ERRORS, session_error
from app.modules.chat.errors import ChatError
from app.modules.chat.schemas import (
    SessionListQuery, SessionListResponse,
    SessionMessagesResponse, SessionResponse,
)


router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("", response_model=SessionListResponse, summary="Listar sessões do chatbot",
            responses={**SESSION_ERRORS, 400: session_error("Cursor invalido para esta consulta.")})
async def list_sessions(
    user: CurrentUserDependency,
    request: Request,
    response: Response,
    query: Annotated[SessionListQuery, Query()],
) -> SessionListResponse:
    """Lista somente sessões do UID do Firebase ID token, incluindo encerradas.

    Contrato compartilhado pelas aplicações web e mobile. As sessões pertencem
    ao usuário autenticado e podem ser acessadas pelas duas plataformas.

    Ordem: updated_at decrescente, depois UUID decrescente. Passe next_cursor
    sem modificá-lo para obter a próxima página, usando o mesmo usuário.
    Não há login adicional nem identificador de usuário na query.
    Títulos (até 80 caracteres) derivam da primeira pergunta; prévias (até 200)
    são texto simples. Uma lista vazia retorna sessions: [] e next_cursor: null.
    A leitura não altera status, datas ou mensagens.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        return await request.app.state.chat_service.list_sessions(
            user, limit=query.limit, cursor=query.cursor,
        )
    except ChatError as error:
        raise HTTPException(error.status_code, error.detail) from None


@router.get("/{session_id}/messages", response_model=SessionMessagesResponse,
            summary="Abrir o histórico de uma sessão",
            responses={**SESSION_ERRORS, 404: session_error("Conversa nao encontrada.")})
async def get_session_messages(
    session_id: UUID,
    user: CurrentUserDependency,
    request: Request,
    response: Response,
) -> SessionMessagesResponse:
    """Carrega o histórico do próprio usuário, inclusive de sessões encerradas.

    A consulta preserva o status. Sessões inexistentes ou de outro UID retornam
    o mesmo 404. Para continuar uma sessão encerrada, chame /iniciar primeiro.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        return await request.app.state.chat_service.messages(session_id, user)
    except ChatError as error:
        raise HTTPException(error.status_code, error.detail) from None


@router.post("/{session_id}/iniciar", response_model=SessionResponse,
             summary="Iniciar ou retomar uma sessão com o mesmo UUID",
             responses={**SESSION_ERRORS, 404: session_error("Conversa nao encontrada."),
                        409: session_error("Conversa ocupada ou em encerramento.",
                                           "Conversa em encerramento. Repita /encerrar antes de retomar.")})
async def start_session(session_id: UUID, user: CurrentUserDependency,
                        request: Request, response: Response):
    """Retoma uma sessão encerrada sem perder mensagens ou trocar seu UUID.

    Retorna uma sessão já ativa e mantém o contrato de criar uma sessão vazia
    para um UUID ainda inexistente. Não aceita UUID pertencente a outro usuário.
    Em encerramento, finalize /encerrar antes de retomar. Não recebe corpo JSON.
    Para uma conversa nova, o cliente pode enviar /chat/messages sem session_id.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        return await request.app.state.chat_service.start(session_id, user)
    except ChatError as error:
        raise HTTPException(error.status_code, error.detail) from None


@router.post("/{session_id}/encerrar", response_model=SessionResponse,
             deprecated=True,
             summary="Encerrar e resumir uma sessão",
             responses={**SESSION_ERRORS, 404: session_error("Conversa nao encontrada."),
                        409: session_error("Aguarde a operacao anterior desta conversa."),
                        502: session_error("Resposta de resumo ou embedding inválida.")})
async def end_session(session_id: UUID, user: CurrentUserDependency,
                      request: Request, response: Response):
    """Encerra sem excluir o histórico; repetir a conclusão retorna o mesmo resultado.

    Endpoint legado de compatibilidade. Novos clientes não precisam encerrar:
    o serviço externo resume sessões após 24h de inatividade sem mudar seu status.

    Em falha durante o encerramento, repita esta chamada para finalizar.
    A sessão continua listada e pode ser retomada por /iniciar. Sem corpo JSON.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        return await request.app.state.chat_service.end(session_id, user)
    except ChatError as error:
        raise HTTPException(error.status_code, error.detail) from None
