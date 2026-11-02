import asyncio
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage
from langgraph.types import Overwrite

from app.core.security import CurrentUser
from app.infrastructure.avatar_urls import resolve_avatar_urls
from app.infrastructure.checkpoints import ConversationCheckpoints, checkpoint_messages
from app.infrastructure.database.sessions import MongoSessions, utc_now
from app.infrastructure.llm.models import AgentModel, LanguageModels
from app.infrastructure.vectorstore.faq import FaqVectors
from app.infrastructure.vectorstore.memory import SummaryVectors
from app.modules.chat.errors import ChatError
from app.modules.chat.formatting import markdown_para_texto_simples
from app.modules.chat.graph import build_chat_graph, _pedido_pdf
from app.modules.chat.session_context import recent_history, earlier_context, recover_rh_query
from app.modules.operational.insights import TOOLS as INSIGHT_TOOLS
from app.modules.chat.analytic_blocks import build_analytic_blocks
from app.modules.chat.pdf_delivery import asks_for_pdf_link, populate_file_blocks
from app.modules.chat.schemas import (
    ChatRequest,
    ChatResponse,
    BlocoArquivo,
    BlocoTexto, BlocoGrafico, BlocoGraficoSetores, BlocoGraficoRosca, BlocoGraficoLinhas, BlocoTabela, ColunaTabela, BlocoGaleriaUsuarios, CelulaImagem, BlocoFichaUsuario, ConformidadeFicha, PontoGrafico,
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
from app.modules.memory.service import ConversationMemory
from app.modules.shared.tools import PDF_LINK_TTL_HOURS
from app.observability.chat import ChatObservation, observe_chat


logger = logging.getLogger(__name__)

CHAT_TIMEZONE = ZoneInfo("America/Sao_Paulo")
MAX_MESSAGES = 200
MAX_SESSION_CHARACTERS = 240000




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
        self.checkpoints = ConversationCheckpoints()
        self.graph = build_chat_graph(
            self.model, self.memory.search, self.faq_vectors.search,
            checkpointer=self.checkpoints.saver,
        )
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
                    raise ChatError(409, "Conversa com encerramento legado pendente. Solicite recuperação ao suporte.")
                if doc.get("status") == "encerrada":
                    await self.checkpoints.discard(user.uid, sid)
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
                    blocos=message.get("blocos", []) if message["role"] == "assistant" else [],
                )
                for message in doc.get("mensagens", [])
                if message.get("role") in {"human", "assistant"}
                and isinstance(message.get("content"), str)
            ]
            source_messages=[message for message in doc.get("mensagens",[])
                             if message.get("role") in {"human","assistant"} and isinstance(message.get("content"),str)]
            for message,source in zip(messages,source_messages):
                populate_file_blocks(message.blocos,(doc.get("pdf_entregas") or {}).get(source.get("pdf_referencia")))
            await self._populate_avatars(messages, user)
            status = doc.get("status", "ativa")
            if status not in {"ativa", "encerrando", "encerrada"}:
                raise ChatError(503, "Historico de conversas indisponivel.")
            return SessionMessagesResponse(
                session_id=session_id,
                status=status,
                total=len(messages),
                mensagens=messages,
            )

    @staticmethod
    async def _populate_avatars(messages, user: CurrentUser):
        blocks = [block for message in messages for block in message.blocos]
        # Galerias antigas continuam legíveis no histórico, com reautorização.
        galleries = [block for block in blocks if isinstance(block, BlocoGaleriaUsuarios)]
        tables = [block for block in blocks if isinstance(block, BlocoTabela)]
        cards = [block for block in blocks if isinstance(block, BlocoFichaUsuario)]
        own_cards = [card for card in cards if card.origem == "usuario_autenticado"]
        if own_cards:
            own_urls = await asyncio.to_thread(resolve_avatar_urls, user, ["__self__"], own=True)
            for card in own_cards:
                card.foto.url = own_urls.get("__self__")
        emails = [card.email for card in cards if card.email and card not in own_cards]
        emails += [item.email for gallery in galleries for item in gallery.imagens if item.email]
        emails += [row.get("email") for table in tables for row in table.linhas
                   if isinstance(row.get("foto"), CelulaImagem) and isinstance(row.get("email"), str)]
        if not emails:
            return
        urls = await asyncio.to_thread(resolve_avatar_urls, user, emails)
        for gallery in galleries:
            for item in gallery.imagens:
                item.url = urls.get(item.email)
        for card in cards:
            if card not in own_cards:
                card.foto.url = urls.get(card.email)
        for table in tables:
            for row in table.linhas:
                photo, email = row.get("foto"), row.get("email")
                if isinstance(photo, CelulaImagem) and isinstance(email, str):
                    photo.url = urls.get(email)

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
            async with (
                self.session_lock(session_id, user.uid) as (doc, token),
                self.checkpoints.turn(self.graph, user.uid, session_id) as checkpoint_turn,
            ):
                if doc.get("status", "ativa") != "ativa":
                    raise ChatError(409, "Conversa encerrada ou em encerramento. "
                                    "Use /iniciar para retomar uma conversa encerrada; "
                                    "solicite recuperação ao suporte se houver encerramento legado pendente.")
                # Ignore drafts for operations removed from the chatbot, including
                # those persisted by previous deployments.
                pending = doc.get("acao_pendente")
                if pending is not None:
                    doc["acao_pendente"] = None
                    await self.repository.update(session_id, user.uid, token, {"acao_pendente": None})
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
                history = recent_history(messages, request.message)
                rh_query = doc.get("consulta_rh") or recover_rh_query(messages)
                session_context = earlier_context(messages, history[-2:], request.message)
                graph_messages = checkpoint_messages(history, len(messages) - len(history))
                graph_messages.append(HumanMessage(content=request.message, id=f"mongo-{len(messages)}"))
                result = await self.graph.ainvoke({
                    "messages": Overwrite(graph_messages),
                    "usuario_atual": user, "session_id": session_id,
                    "historico": history,
                    "contexto_sessao": session_context,
                    "mensagem": request.message,
                    "contexto": {
                        "workspace_id": None, "data_hora": datetime.now(CHAT_TIMEZONE).isoformat(),
                        "fuso": CHAT_TIMEZONE.key, "ultima_rota": doc.get("ultima_rota", ""),
                        "possui_acao_pendente": bool(doc.get("acao_pendente")),
                        "consulta_operacional": doc.get("consulta_operacional"),
                        "consulta_rh": rh_query,
                        "formato_resposta": "markdown" if markdown else "texto_simples",
                        "ferramentas_disponiveis": [
                            "buscar_historico", "consultar_normas",
                            "buscar_outros_usuarios", "buscar_meus_dados", "consultar_nrs",
                            "consultar_nrs_obrigatorias", "consultar_situacao_nrs",
                            "consultar_nrs_organizacao",
                            "consultar_orientacoes_sst", "consultar_fontes_publicas",
                            "consultar_conversas", "consultar_notificacoes",
                            "consultar_acessos", "consultar_indicadores_equipe",
                            "consultar_grafico_pendencias_nr", "consultar_grafico_vencimentos",
                            "consultar_vencimentos_conformidades", "consultar_pendencias_equipe",
                            "consultar_conformidade_unidade", "consultar_formularios_pendentes",
                            "consultar_respostas_formulario", "consultar_detalhes_evento",
                            "consultar_candidatos_treinamento",
                            "consultar_treinamentos",
                            "consultar_eventos",
                            "gerar_pdf",
                            *[item.name for item in INSIGHT_TOOLS],
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
                                   "permitidos pelo perfil autenticado. O chatbot nao cria nem envia mensagens. "
                                   "A consulta de conversas acessa apenas mensagens entre o usuario "
                                   "autenticado e uma pessoa do mesmo workspace. "
                                   "Notificacoes podem ser consultadas somente para o usuario "
                                   "autenticado, sem inferir leitura ou pendencia. "
                                   "Acessos mostram apenas dias registrados para o proprio "
                                   "usuario, sem contar logins individuais ou horarios. "
                                   "Treinamentos atribuidos ao usuario podem ser consultados "
                                   "pelo agente de agenda, sem inferir inscricoes a partir da NR. "
                                   "A Agenda consulta somente eventos internos e treinamentos "
                                   "atribuídos ao próprio usuário. Não há integração com calendários externos. "
                                   "PDFs podem ser gerados da resposta revisada quando pedidos "
                                   "explicitamente; a aplicacao fornece o link temporario. "
                                   "NRs da unidade ou empresa usam vínculos unidade_nr no PostgreSQL "
                                   "do workspace autenticado, não o catálogo público genérico. "
                                   "Nao ha operacoes de escrita. NRs usam primeiro o portal "
                                   "oficial do MTE via MCP Fetch; a collection MongoDB autorizada "
                                   "serve como contexto interno e fallback. "
                                   "Historico nao comprova direitos nem execucao. "
                                   "Nao expor UID, tokens, workspace_id ou chaves internas. "
                                   "Referencias evento_id de eventos autorizados podem ser exibidas para selecao; "
                                   "nao concedem acesso nem substituem verificacoes de permissao. "
                                   "Em respostas do FAQ, citar documento e pagina quando "
                                   "fornecidos pelos trechos autorizados.",
                    },
                    "memoria": {}, "busca_memoria": "", "memoria_consultada": False,
                    "rota": "", "resultado": {}, "resultado_tool": {}, "rh_decision": None,
                    "rh_route": "", "sst_decision": None, "sst_route": "",
                    "agenda_decision": None, "agenda_route": "",
                    "operational_decision": None, "roteador_decision": None, "acao_pendente": doc.get("acao_pendente"),
                    "candidato": "", "avaliacao_juiz": {},
                    "resposta": "",
                    "agentes_chamados": [], "guardar_turno": False,
                    "pdf_solicitado": False, "pdf_url": None, "pdf_metadata": None,
                    "resposta_deterministica": False,
                    "consulta_rh": rh_query,
                    "eventos_referencias": doc.get("eventos_referencias", []),
                    "ultimo_pdf": previous_pdf,
                }, config=checkpoint_turn.config)
                observation.mark_result(result)
                pdf_url = result.get("pdf_url")
                public_answer = result["resposta"]
                if not markdown:
                    public_answer = markdown_para_texto_simples(public_answer)
                stored_answer = public_answer
                pdf_delivery=None
                if pdf_url:
                    pdf_delivery=({**(result.get("pdf_metadata") or {}),"url":pdf_url,
                                   "expira_em":utc_now()+timedelta(hours=PDF_LINK_TTL_HOURS)}
                                  if result.get("pdf_solicitado") else dict(previous_pdf or {}))
                    pdf_delivery.setdefault("id",str(uuid4()))
                    stored_answer += "\n\nPDF gerado e link temporário entregue."
                # The graph has already released this authorized deterministic result.
                # Transport conversion must not change eligibility for visual blocks.
                structured_ready = (result.get("guardar_turno")
                    and result.get("resposta") == result.get("candidato")
                    and result.get("avaliacao_juiz", {}).get("status") == "aprovado")
                blocks = []
                chart_data = result.get("resultado_tool", {})
                if (structured_ready
                        and result.get("resultado", {}).get("evidencia_tool", {}).get("nome") in {"consultar_grafico_treinamentos", "consultar_evolucao_treinamentos"}
                        and chart_data.get("status") == "ok"):
                    counts = chart_data["categorias"]
                    kind = chart_data.get("tipo_grafico", "grafico_barras")
                    graph_class = {"grafico_barras": BlocoGrafico, "grafico_setores": BlocoGraficoSetores, "grafico_rosca": BlocoGraficoRosca, "grafico_linhas": BlocoGraficoLinhas}.get(kind)
                    if graph_class is None:
                        raise ChatError(502, "Tipo de gráfico inválido.")
                    title = "Suas conclusões por mês" if kind == "grafico_linhas" else "Seus treinamentos por situação"
                    introduction = "Veja a evolução mensal das suas conclusões em eventos ativos:" if kind == "grafico_linhas" else "Veja a distribuição dos seus treinamentos atribuídos em eventos ativos:"
                    blocks = [BlocoTexto(conteudo=introduction),
                              graph_class(titulo=title, fonte=chart_data["fonte"],
                                           dados=[PontoGrafico(categoria=k, valor=v) for k, v in counts.items()]),
                              BlocoTexto(conteudo=public_answer)]
                if (structured_ready
                        and result.get("resultado", {}).get("evidencia_tool", {}).get("nome") == "buscar_outros_usuarios"
                        and chart_data.get("status") == "ok" and chart_data.get("consulta") == "listar"
                        and chart_data.get("usuarios")):
                    columns = [("foto", "Foto"), ("nome", "Nome"), ("email", "E-mail"), ("cargo", "Cargo"),
                               ("unidade", "Unidade"), ("tipo", "Perfil"), ("status", "Situação")]
                    rows = []
                    for item in chart_data["usuarios"]:
                        row = {key: item.get(key) for key, _ in columns if key != "foto"}
                        row["foto"] = CelulaImagem(descricao=f"Foto de {item['nome']}"[:280])
                        rows.append(row)
                    blocks = [BlocoTexto(conteudo="Veja os usuários encontrados no seu escopo:"),
                              BlocoTabela(titulo="Usuários", versao=2 if "retornada" in chart_data else 1,
                                          paginacao="local" if "retornada" in chart_data else None,
                                          **{key: chart_data.get(key) for key in ("solicitada", "retornada", "limitado", "tem_mais", "motivo_limite")}, colunas=[ColunaTabela(chave=key, titulo=label) for key, label in columns],
                                          linhas=rows, pagina=chart_data["pagina"], total_paginas=chart_data["total_paginas"], total=chart_data["total"]),
                              BlocoTexto(conteudo=public_answer)]
                if (structured_ready
                        and result.get("resultado", {}).get("evidencia_tool", {}).get("nome") == "buscar_meus_dados"
                        and chart_data.get("status") == "ok"):
                    from app.modules.chat.subgraphs import _campo_dado_proprio
                    if _campo_dado_proprio(request.message) is None:
                        data = chart_data.get("dados", {})
                        blocks = [BlocoTexto(conteudo="Estes são os seus dados:"),
                                  BlocoFichaUsuario(titulo="Meu perfil", origem="usuario_autenticado",
                                      nome=data.get("nome"), email=data.get("email"), perfil=data.get("tipo"),
                                      cargo=data.get("cargo"), unidade=data.get("unidade"), modalidade=data.get("modalidade"),
                                      foto=CelulaImagem(descricao="Foto do usuário autenticado"))]
                if (structured_ready
                        and result.get("resultado", {}).get("evidencia_tool", {}).get("nome") == "consultar_conformidade_usuario"
                        and chart_data.get("status") == "ok"):
                    nrs = chart_data.get("nrs", [])
                    total = len(nrs)
                    valid = sum(nr.get("situacao") == "VIGENTE" for nr in nrs)
                    name = chart_data["usuario"]
                    blocks = [BlocoFichaUsuario(
                        nome=name, email=chart_data.get("email"), cargo=chart_data.get("cargo"),
                        unidade=chart_data.get("unidade"), foto=CelulaImagem(descricao=f"Foto de {name}"),
                        conformidade=ConformidadeFicha(percentual=round(100 * valid / total, 2) if total else None,
                                                       vigentes=valid, total=total, data_referencia=chart_data.get("data_referencia"))),
                              BlocoTexto(conteudo=public_answer)]
                if (structured_ready
                        and result.get("rota") == "consulta_operacional" and chart_data.get("status") == "ok"
                        and chart_data.get("linhas") and chart_data.get("paginacao") != "local"):
                    compact_expiry = result.get("resultado", {}).get("evidencia_tool", {}).get("nome") == "consultar_grafico_vencimentos"
                    event_details = result.get("resultado", {}).get("evidencia_tool", {}).get("nome") == "consultar_detalhes_evento"
                    blocks = [] if compact_expiry else [BlocoTexto(conteudo=public_answer if event_details else chart_data["titulo"])]
                    blocks.append(BlocoTabela(
                        titulo="Detalhamento por faixa de prazo" if compact_expiry else chart_data["titulo"],
                        colunas=chart_data["colunas"], linhas=chart_data["linhas"],
                        pagina=chart_data["pagina"], total_paginas=chart_data["total_paginas"], total=chart_data["total"]))
                    if chart_data.get("grafico"):
                        chart_class = {"grafico_setores": BlocoGraficoSetores, "grafico_linhas": BlocoGraficoLinhas, "grafico_rosca": BlocoGraficoRosca}.get(chart_data.get("tipo_grafico"), BlocoGrafico)
                        blocks.append(chart_class(titulo=chart_data["titulo"], dados=chart_data["grafico"],
                                                  unidade=chart_data.get("unidade_grafico", "pessoas"), fonte=chart_data["fonte"]))
                    summary = f"Retornados {len(chart_data['linhas'])} de {chart_data['total']} registro(s) disponíveis."
                    if compact_expiry:
                        summary = ("Os números representam registros de conformidade, não pessoas diferentes. "
                                   f"Fonte: {chart_data['fonte']}.")
                        dates = {row["data_referencia"] for row in chart_data["linhas"] if row.get("data_referencia")}
                        if len(dates) == 1:
                            from app.modules.chat.subgraphs import _data_legivel
                            reference = _data_legivel(next(iter(dates)))
                            if reference:
                                summary += f" Dados de {reference}."
                        elif dates:
                            summary += " Referências disponíveis na tabela."
                    if len(chart_data["linhas"]) < chart_data["total"]:
                        summary += " O conjunto retornado é parcial."
                    if chart_data.get("aviso"):
                        summary += " " + chart_data["aviso"]
                    blocks.append(BlocoTexto(conteudo=summary))
                if (structured_ready and result.get("rota") == "consulta_operacional"
                        and chart_data.get("status") in {"ok","sem_dados"}
                        and chart_data.get("paginacao") == "local"):
                    blocks=build_analytic_blocks(
                        result.get("resultado",{}).get("evidencia_tool",{}).get("nome"),chart_data)
                logger.info("chat_blocks_ready tool=%s judge=%s types=%s",
                            result.get("resultado", {}).get("evidencia_tool", {}).get("nome", "none"),
                            result.get("avaliacao_juiz", {}).get("status", "none"),
                            ",".join(block.tipo for block in blocks) or "texto")
                if result.get("rota")=="consulta_operacional" and not blocks:
                    blocks=[BlocoTexto(conteudo=public_answer[i:i+6000]) for i in range(0,len(public_answer),6000)]
                if (result.get("rota")=="agenda" and not blocks
                        and result.get("resultado",{}).get("evidencia_tool",{}).get("nome") in {"consultar_eventos","consultar_treinamentos"}):
                    blocks=[BlocoTexto(conteudo=public_answer[i:i+6000]) for i in range(0,len(public_answer),6000)]
                if pdf_delivery:
                    if not blocks:
                        blocks=[BlocoTexto(conteudo=public_answer[i:i+6000]) for i in range(0,len(public_answer),6000)]
                    blocks.append(BlocoArquivo(id=pdf_delivery["id"],nome=pdf_delivery.get("nome","consulta-astro.pdf"),
                                              tamanho_bytes=pdf_delivery.get("tamanho_bytes"),
                                              expira_em=pdf_delivery["expira_em"],url=None))
                # Persist the same public blocks sent by POST, including text-only replies.
                if not blocks:
                    blocks = [BlocoTexto(conteudo=public_answer[i:i + 6000]) for i in range(0, len(public_answer), 6000)]
                response = ChatResponse(session_id=session_id,
                                        agentes_chamados=result["agentes_chamados"], blocos=blocks)
                if result["guardar_turno"]:
                    delivery_fields = {}
                    if pdf_delivery:
                        delivery_fields["pdf_entregas"]={**(doc.get("pdf_entregas") or {}),pdf_delivery["id"]:pdf_delivery}
                    if result.get("pdf_solicitado"):
                        delivery_fields["ultimo_pdf"] = pdf_delivery or {"url":None,"expira_em":None}
                    await self.repository.update(session_id, user.uid, token,
                        {
                            **delivery_fields,
                            "titulo": session_title(doc, request.message),
                            "ultima_mensagem_previa": compact_session_text(stored_answer, 200),
                            "ultima_rota": result["rota"],
                            "acao_pendente": result.get("acao_pendente"),
                            "consulta_rh": result.get("consulta_rh"),
                            "consulta_operacional": (result.get("operational_decision") if result.get("rota") == "consulta_operacional"
                                                       else doc.get("consulta_operacional")),
                            "eventos_referencias": result.get("eventos_referencias", []),
                        }, messages=[
                            {"role": "human", "content": request.message},
                            # Persist the public wire representation: date-only fields
                            # must remain ISO strings, which BSON can encode losslessly.
                            {"role": "assistant", "content": stored_answer,
                             **({"pdf_referencia":pdf_delivery["id"]} if pdf_delivery else {}),
                             **({"blocos": [block.model_dump(mode="json") for block in blocks]} if blocks else {})},
                        ])
                    # Compact only after the atomic Mongo update succeeds. An
                    # old checkpoint never overrides data loaded from another worker.
                    public_history = recent_history(messages + [
                        {"role": "human", "content": request.message},
                        {"role": "assistant", "content": stored_answer},
                    ])
                    await checkpoint_turn.commit(public_history, len(messages) + 2 - len(public_history))
                # Avatar URLs are transient. PDF URLs live only in private delivery
                # metadata; public blocks/history and model input never persist them.
                await self._populate_avatars([response], user)
                populate_file_blocks(response.blocos,pdf_delivery)
                return response

    async def end(self, session_id: UUID, user: CurrentUser):
        """Internal legacy closure/recovery; not exposed by an HTTP route."""
        sid = str(session_id)
        async with self.operation():
            async with self.session_lock(sid, user.uid) as (doc, token):
                if doc.get("status") == "encerrada":
                    await self.checkpoints.discard(user.uid, sid)
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
                await self.checkpoints.discard(user.uid, sid)
                return SessionResponse(session_id=session_id, status="encerrada",
                                       resumo=summary or None, resumo_indexado=indexed)

    async def close(self):
        self.checkpoints.clear()
        try:
            try:
                await self.repository.close()
            finally:
                await self.vectors.close()
        finally:
            await self.faq_vectors.close()
