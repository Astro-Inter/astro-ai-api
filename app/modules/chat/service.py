import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.core.security import CurrentUser
from app.infrastructure.database.sessions import MongoSessions, utc_now
from app.infrastructure.llm.models import AgentModel, LanguageModels
from app.infrastructure.vectorstore.faq import FaqVectors
from app.infrastructure.vectorstore.memory import SummaryVectors
from app.modules.chat.errors import ChatError
from app.modules.chat.formatting import markdown_para_texto_simples
from app.modules.chat.graph import build_chat_graph, _pedido_pdf
from app.modules.chat.pdf_delivery import asks_for_pdf_link
from app.modules.chat.schemas import (
    ChatRequest,
    ChatResponse,
    SessionMessage,
    SessionMessagesResponse,
    SessionResponse,
    SessionListResponse,
    SessionSummary,
)
from app.modules.chat.session_listing import (
    compact_session_text, decode_session_cursor, encode_session_cursor,
    session_preview, session_title,
)
from app.modules.chat.ui_actions import normalizar_acoes_interface
from app.modules.memory.service import ConversationMemory
from app.modules.shared.tools import PDF_LINK_TTL_HOURS
from app.observability.chat import ChatObservation, observe_chat


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
            sid = str(session_id)
            await self.repository.ensure(sid, user.uid)
            async with self.session_lock(sid, user.uid) as (doc, token):
                if doc.get("status") == "encerrando":
                    raise ChatError(409, "Conversa em encerramento. Repita /encerrar antes de retomar.")
                if doc.get("status") == "encerrada":
                    # Mantém UUID/histórico; o próximo encerramento precisa de novo resumo.
                    await self.repository.update(sid, user.uid, token, {
                        "status": "ativa", "encerrada_em": None, "resumo": "",
                        "resumo_indexado": False, "resumo_parcial": "", "resumo_ate": 0,
                        "acao_pendente": None,
                    })
            return SessionResponse(session_id=session_id, status="ativa", resumo=None)

    async def list_sessions(self, user: CurrentUser, *, limit: int = 20, cursor: str | None = None):
        before = decode_session_cursor(cursor, user.uid)
        async with self.operation():
            docs = await self.repository.list_sessions(user.uid, limit, before)
            sessions = [SessionSummary(
                session_id=doc["_id"], title=session_title(doc),
                last_message_preview=session_preview(doc),
                created_at=doc["iniciada_em"], updated_at=doc["atualizada_em"],
                status=doc.get("status", "ativa"),
            ) for doc in docs[:limit]]
            next_cursor = None
            if len(docs) > limit:
                last = sessions[-1]
                next_cursor = encode_session_cursor(last.updated_at, last.session_id, user.uid)
            return SessionListResponse(sessions=sessions, next_cursor=next_cursor)

    async def messages(self, session_id: UUID, user: CurrentUser) -> SessionMessagesResponse:
        async with self.operation():
            doc = await self.repository.get(str(session_id), user.uid)
            messages = [
                SessionMessage(
                    role="user" if message["role"] == "human" else "assistant",
                    content=message["content"],
                )
                for message in doc.get("mensagens", [])
                if message.get("role") in {"human", "assistant"}
                and isinstance(message.get("content"), str)
            ]
            status = doc.get("status", "ativa")
            if status not in {"ativa", "encerrando", "encerrada"}:
                raise ChatError(503, "Historico de conversas indisponivel.")
            return SessionMessagesResponse(
                session_id=session_id,
                status=status,
                total=len(messages),
                mensagens=messages,
            )

    async def chat(
        self, request: ChatRequest, user: CurrentUser, *, markdown: bool = True,
    ) -> ChatResponse:
        session_id = str(request.session_id or uuid4())
        with observe_chat(
            reused_session=request.session_id is not None,
            markdown=markdown,
        ) as observation:
            return await self._chat(request, user, session_id, markdown, observation)

    async def _chat(
        self,
        request: ChatRequest,
        user: CurrentUser,
        session_id: str,
        markdown: bool,
        observation: ChatObservation,
    ) -> ChatResponse:
        async with self.operation():
            await self.repository.ensure(session_id, user.uid)
            async with self.session_lock(session_id, user.uid) as (doc, token):
                if doc.get("status", "ativa") != "ativa":
                    raise ChatError(409, "Conversa encerrada ou em encerramento. "
                                    "Use /iniciar para retomar uma conversa encerrada; "
                                    "repita /encerrar se o encerramento estiver pendente.")
                messages = doc.get("mensagens", [])
                previous_pdf = doc.get("ultimo_pdf")
                if previous_pdf is None and asks_for_pdf_link(request.message):
                    last_question = next((m["content"] for m in reversed(messages) if m["role"] == "human"), "")
                    if _pedido_pdf(last_question):
                        # Sessions from earlier deployments have no delivery metadata.
                        previous_pdf = {"sem_metadados": True}
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
                        "possui_acao_pendente": bool(doc.get("acao_pendente")),
                        "formato_resposta": "markdown" if markdown else "texto_simples",
                        "ferramentas_disponiveis": [
                            "buscar_historico", "consultar_normas",
                            "buscar_outros_usuarios", "buscar_meus_dados", "consultar_nrs",
                            "consultar_nrs_obrigatorias", "consultar_situacao_nrs",
                            "consultar_nrs_organizacao",
                            "consultar_orientacoes_sst", "consultar_fontes_publicas",
                            "enviar_mensagem", "consultar_conversas", "consultar_notificacoes",
                            "consultar_acessos",
                            "consultar_treinamentos",
                            "consultar_eventos",
                            "consultar_google_calendar", "criar_evento_google_calendar",
                            "gerar_pdf",
                        ],
                        "fontes_disponiveis": [
                            "faq_chunks", "nrs", "portal_oficial_mte_via_mcp_fetch",
                            "mte", "fundacentro", "anvisa",
                        ],
                        "limites": "Somente memoria de conversas do proprio usuario esta disponivel. "
                                   "Politicas internas podem ser consultadas apenas na base FAQ "
                                   "autorizada; legislação e publicações externas usam somente "
                                   "catálogos oficiais previamente cadastrados. "
                                   "O agente de RH pode consultar somente os dados de usuarios "
                                   "permitidos pelo perfil autenticado. O envio de mensagens exige "
                                   "destinatario do mesmo workspace, previa e confirmacao explicita. "
                                   "A consulta de conversas acessa apenas mensagens entre o usuario "
                                   "autenticado e uma pessoa do mesmo workspace. "
                                   "Notificacoes podem ser consultadas somente para o usuario "
                                   "autenticado, sem inferir leitura ou pendencia. "
                                   "Acessos mostram apenas dias registrados para o proprio "
                                   "usuario, sem contar logins individuais ou horarios. "
                                   "Treinamentos atribuidos ao usuario podem ser consultados "
                                   "pelo agente de agenda, sem inferir inscricoes a partir da NR. "
                                   "Perguntas sobre eventos sem mencionar Google consultam "
                                   "eventos do Astro nas turmas atribuídas ao próprio usuário. "
                                   "O Google Calendar e opcional e conectado sob demanda por OAuth. "
                                   "Consultar ou criar eventos usa apenas o calendario principal "
                                   "do usuario; criacao exige previa e confirmacao explicita. "
                                   "PDFs podem ser gerados da resposta revisada quando pedidos "
                                   "explicitamente; a aplicacao fornece o link temporario. "
                                   "NRs da unidade ou empresa usam vínculos unidade_nr no PostgreSQL "
                                   "do workspace autenticado, não o catálogo público genérico. "
                                   "Nao ha outras operacoes de escrita. NRs usam primeiro o portal "
                                   "oficial do MTE via MCP Fetch; a collection MongoDB autorizada "
                                   "serve como contexto interno e fallback. "
                                   "Historico nao comprova direitos nem execucao. "
                                   "Nao expor identificadores internos nem metadados tecnicos. "
                                   "Em respostas do FAQ, citar documento e pagina quando "
                                   "fornecidos pelos trechos autorizados.",
                    },
                    "memoria": {}, "busca_memoria": "", "memoria_consultada": False,
                    "rota": "", "resultado": {}, "resultado_tool": {}, "rh_decision": None,
                    "rh_route": "", "sst_decision": None, "sst_route": "",
                    "agenda_decision": None, "agenda_route": "",
                    "roteador_decision": None, "acao_pendente": doc.get("acao_pendente"),
                    "confirmacao_explicita": False,
                    "candidato": "", "avaliacao_juiz": {},
                    "resposta": "",
                    "agentes_chamados": [], "guardar_turno": False,
                    "pdf_solicitado": False, "pdf_url": None,
                    "resposta_deterministica": False,
                    "consulta_rh": doc.get("consulta_rh"),
                    "ultimo_pdf": previous_pdf,
                }, config={"recursion_limit": 20})
                observation.mark_result(result)
                pdf_url = result.get("pdf_url")
                public_answer = normalizar_acoes_interface(result["resposta"])
                if not markdown:
                    public_answer = markdown_para_texto_simples(public_answer)
                stored_answer = public_answer
                if pdf_url:
                    validity = (
                        f"link válido por {PDF_LINK_TTL_HOURS} horas"
                        if result.get("pdf_solicitado")
                        else "link temporário; o prazo original não é renovado"
                    )
                    if markdown:
                        public_answer += (
                            f"\n\n[Baixar PDF]({pdf_url}) "
                            f"({validity})."
                        )
                    else:
                        public_answer += (
                            f"\n\nBaixar PDF: {pdf_url}\n"
                            f"{validity.capitalize()}."
                        )
                    stored_answer += "\n\nPDF gerado e link temporário entregue."
                response = ChatResponse(session_id=session_id, resposta=public_answer,
                                        agentes_chamados=result["agentes_chamados"])
                if result["guardar_turno"]:
                    delivery_fields = {}
                    if result.get("pdf_solicitado"):
                        delivery_fields["ultimo_pdf"] = {
                            "url": pdf_url,
                            "expira_em": utc_now() + timedelta(hours=PDF_LINK_TTL_HOURS) if pdf_url else None,
                        }
                    await self.repository.update(session_id, user.uid, token,
                        {
                            **delivery_fields,
                            "titulo": session_title(doc, request.message),
                            "ultima_mensagem_previa": compact_session_text(stored_answer, 200),
                            "ultima_rota": result["rota"],
                            "acao_pendente": result.get("acao_pendente"),
                            "consulta_rh": result.get("consulta_rh"),
                        }, messages=[
                            {"role": "human", "content": request.message},
                            {"role": "assistant", "content": stored_answer},
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
                last_message = doc.get("ultima_mensagem_em", doc["atualizada_em"])
                if "resumo_mensagens" in doc and (
                    doc["resumo_mensagens"] != len(doc.get("mensagens", []))
                    or doc.get("resumo_ultima_mensagem_em") != last_message
                ):
                    summary = ""
                if doc.get("mensagens"):
                    if not summary:
                        summary = await self.memory.summarize(doc, token)
                    await self.repository.update(sid, user.uid, token, {
                        "resumo": summary, "resumo_mensagens": len(doc["mensagens"]),
                        "resumo_ultima_mensagem_em": last_message,
                        "ultima_mensagem_em": last_message,
                    })
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
