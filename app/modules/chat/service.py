import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from time import monotonic
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.core.security import CurrentUser
from app.infrastructure.llm.models import AgentModel, LanguageModels
from app.modules.chat.errors import ChatError
from app.modules.chat.graph import build_chat_graph
from app.modules.chat.schemas import ChatRequest, ChatResponse


CHAT_TIMEZONE = ZoneInfo("America/Sao_Paulo")


@dataclass
class Conversation:
    owner_uid: str
    history: list[dict[str, str]] = field(default_factory=list)
    last_route: str = ""
    touched: float = field(default_factory=monotonic)
    busy: bool = False


class ChatService:
    """Memória limitada, local a um processo. Sem persistência ou acesso a bancos."""

    def __init__(
        self, model: AgentModel | None = None, *, max_sessions: int = 1000,
        ttl: float = 3600, request_timeout: float = 120,
    ):
        self.graph = build_chat_graph(model or LanguageModels())
        self.sessions: dict[UUID, Conversation] = {}
        self.max_sessions = max_sessions
        self.ttl = ttl
        self.request_timeout = request_timeout
        self.active_requests = 0

    async def chat(self, request: ChatRequest, user: CurrentUser) -> ChatResponse:
        now = monotonic()
        for key, value in list(self.sessions.items()):
            if not value.busy and now - value.touched > self.ttl:
                del self.sessions[key]
        new_session = request.session_id is None
        session_id = request.session_id or uuid4()
        if new_session:
            if len(self.sessions) >= self.max_sessions:
                raise ChatError(503, "Limite temporario de conversas atingido.")
            session = Conversation(owner_uid=user.uid)
        else:
            session = self.sessions.get(session_id)
            if session is None or session.owner_uid != user.uid:
                # Mesma resposta para sessão alheia, inexistente e expirada.
                raise ChatError(404, "Conversa nao encontrada ou expirada.")
        if session.busy:
            raise ChatError(409, "Aguarde a resposta anterior desta conversa.")
        if self.active_requests >= 20:
            raise ChatError(429, "Muitas mensagens simultaneas. Tente novamente.")
        # Até aqui não há await: reserva e liberação são atômicas no event loop.
        self.sessions[session_id] = session
        session.busy = True
        self.active_requests += 1
        try:
            async with asyncio.timeout(self.request_timeout):
                result = await self.graph.ainvoke({
                    "historico": list(session.history),
                    "mensagem": request.message,
                    "contexto": {
                        "uid": user.uid, "role": user.role, "workspace_id": None,
                        "data_hora": datetime.now(CHAT_TIMEZONE).isoformat(),
                        "fuso": CHAT_TIMEZONE.key, "ultima_rota": session.last_route,
                        "ferramentas_disponiveis": [], "fontes_disponiveis": [],
                        "limites": "Nesta etapa nao ha consulta de registros ou normas, "
                                   "nem execucao de operacoes. Somente orientacao geral, "
                                   "esclarecimento ou aviso de indisponibilidade. "
                                   "Nao incluir escrita, fontes ou evento na saida.",
                    },
                    "rota": "", "resultado": {}, "candidato": "", "resposta": "",
                    "agentes_chamados": [], "guardar_turno": False,
                }, config={"recursion_limit": 20})
            response = ChatResponse(
                session_id=session_id, resposta=result["resposta"],
                agentes_chamados=result["agentes_chamados"],
            )
            if result["guardar_turno"]:
                session.history.extend([
                    {"role": "user", "content": request.message},
                    {"role": "assistant", "content": response.resposta},
                ])
                # Mantém pares completos, no máximo 10 turnos e 24 mil caracteres.
                while len(session.history) > 20 or sum(
                    len(message["content"]) for message in session.history
                ) > 24000:
                    del session.history[:2]
                session.last_route = result["rota"]
            return response
        except TimeoutError:
            if new_session:
                self.sessions.pop(session_id, None)
            raise ChatError(504, "A IA excedeu o tempo de resposta. Tente novamente.") from None
        except BaseException:
            if new_session:
                self.sessions.pop(session_id, None)
            raise
        finally:
            session.busy = False
            session.touched = monotonic()
            self.active_requests -= 1
