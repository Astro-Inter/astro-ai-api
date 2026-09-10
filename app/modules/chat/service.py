import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.core.security import CurrentUser
from app.infrastructure.database.sessions import MongoSessions, utc_now
from app.infrastructure.llm.models import AgentModel, LanguageModels
from app.infrastructure.vectorstore.faq import FaqVectors
from app.infrastructure.vectorstore.memory import SummaryVectors
from app.modules.chat.errors import ChatError
from app.modules.chat.graph import build_chat_graph
from app.modules.chat.schemas import ChatRequest, ChatResponse, SessionResponse
from app.modules.memory.service import ConversationMemory


CHAT_TIMEZONE = ZoneInfo("America/Sao_Paulo")
MAX_MESSAGES = 200
MAX_SESSION_CHARACTERS = 240000


def recent_history(messages):
    history = [{"role": "user" if m["role"] == "human" else "assistant", "content": m["content"]}
               for m in messages[-20:]]
    while sum(len(message["content"]) for message in history) > 24000:
        del history[:2]
    return history


class ChatService:
    def __init__(self, model: AgentModel | None = None, *, repository=None,
                 vectors=None, faq_vectors=None, request_timeout: float = 120):
        if not 0 < request_timeout <= 120:
            raise ValueError("Timeout deve estar entre 0 e 120 segundos.")
        self.model = model or LanguageModels()
        self.repository = repository if repository is not None else MongoSessions()
        self.vectors = vectors if vectors is not None else SummaryVectors()
        self.faq_vectors = faq_vectors if faq_vectors is not None else FaqVectors()
        self.memory = ConversationMemory(self.repository, self.vectors, self.model)
        self.graph = build_chat_graph(self.model, self.memory.search, self.faq_vectors.search)
        self.request_timeout = request_timeout
        self.active_requests = 0

    @asynccontextmanager
    async def operation(self):
        if self.active_requests >= 20:
            raise ChatError(429, "Muitas operacoes simultaneas. Tente novamente.")
        self.active_requests += 1
        try:
            async with asyncio.timeout(self.request_timeout):
                yield
        except TimeoutError:
            raise ChatError(504, "Operacao excedeu o tempo de resposta. Tente novamente.") from None
        finally:
            self.active_requests -= 1

    @asynccontextmanager
    async def session_lock(self, session_id: str, uid: str):
        doc, token = await self.repository.acquire(session_id, uid)
        try:
            yield doc, token
        finally:
            try:
                await self.repository.release(session_id, uid, token)
            except ChatError:
                # O lease expira sozinho se o banco cair durante a liberação.
                pass

    async def start(self, session_id: UUID, user: CurrentUser):
        async with self.operation():
            doc = await self.repository.ensure(str(session_id), user.uid)
            if doc.get("status", "ativa") != "ativa":
                raise ChatError(409, "Conversa encerrada ou em encerramento. Use um novo session_id.")
            return SessionResponse(session_id=session_id, status="ativa", resumo=None)

    async def chat(self, request: ChatRequest, user: CurrentUser) -> ChatResponse:
        session_id = str(request.session_id or uuid4())
        async with self.operation():
            await self.repository.ensure(session_id, user.uid)
            async with self.session_lock(session_id, user.uid) as (doc, token):
                if doc.get("status", "ativa") != "ativa":
                    raise ChatError(409, "Conversa encerrada ou em encerramento. Use um novo session_id.")
                messages = doc.get("mensagens", [])
                if (len(messages) >= MAX_MESSAGES or sum(len(m["content"]) for m in messages)
                        + len(request.message) + 6000 > MAX_SESSION_CHARACTERS):
                    raise ChatError(409, "Limite da conversa atingido. Encerre e inicie outra sessao.")
                result = await self.graph.ainvoke({
                    "usuario_atual": user, "session_id": session_id,
                    "historico": recent_history(messages),
                    "mensagem": request.message,
                    "contexto": {
                        "workspace_id": None, "data_hora": datetime.now(CHAT_TIMEZONE).isoformat(),
                        "fuso": CHAT_TIMEZONE.key, "ultima_rota": doc.get("ultima_rota", ""),
                        "ferramentas_disponiveis": ["buscar_historico", "consultar_normas"],
                        "fontes_disponiveis": ["faq_chunks"],
                        "limites": "Somente memoria de conversas do proprio usuario esta disponivel. "
                                   "Normas podem ser consultadas apenas na base FAQ autorizada. "
                                   "Nao ha consulta de registros de negocio nem execucao de operacoes. "
                                   "Historico nao comprova direitos nem execucao. "
                                   "Nao incluir escrita, fontes ou evento na saida.",
                    },
                    "memoria": {}, "busca_memoria": "", "memoria_consultada": False,
                    "rota": "", "resultado": {}, "candidato": "", "avaliacao_juiz": {},
                    "resposta": "",
                    "agentes_chamados": [], "guardar_turno": False,
                }, config={"recursion_limit": 20})
                response = ChatResponse(session_id=session_id, resposta=result["resposta"],
                                        agentes_chamados=result["agentes_chamados"])
                if result["guardar_turno"]:
                    await self.repository.update(session_id, user.uid, token,
                        {"ultima_rota": result["rota"]}, messages=[
                            {"role": "human", "content": request.message},
                            {"role": "assistant", "content": response.resposta},
                        ])
                return response

    async def end(self, session_id: UUID, user: CurrentUser):
        sid = str(session_id)
        async with self.operation():
            async with self.session_lock(sid, user.uid) as (doc, token):
                if doc.get("status") == "encerrada":
                    return SessionResponse(session_id=session_id, status="encerrada",
                        resumo=doc.get("resumo") or None, resumo_indexado=doc.get("resumo_indexado", False))
                await self.repository.update(sid, user.uid, token, {"status": "encerrando"})
                summary = doc.get("resumo") or ""
                if doc.get("mensagens"):
                    if not summary:
                        summary = await self.memory.summarize(doc, token)
                        await self.repository.update(sid, user.uid, token, {"resumo": summary})
                    # ID estável: repetir após falha nunca duplica o ponto vetorial.
                    await self.vectors.upsert({**doc, "resumo": summary})
                indexed = bool(doc.get("mensagens"))
                await self.repository.update(sid, user.uid, token, {
                    "status": "encerrada", "encerrada_em": utc_now(), "resumo_indexado": indexed,
                    "resumo_parcial": "", "resumo_ate": 0,
                })
                return SessionResponse(session_id=session_id, status="encerrada",
                                       resumo=summary or None, resumo_indexado=indexed)

    async def close(self):
        try:
            try:
                await self.repository.close()
            finally:
                await self.vectors.close()
        finally:
            await self.faq_vectors.close()
