import logging
import re
import unicodedata
from datetime import date, datetime

from pydantic import ValidationError

from langgraph.graph import END, START, StateGraph

from app.infrastructure.llm.models import AgentModel
from app.modules.agenda.tools import AgendaToolDecision, CriarEventoGoogleArgs
from app.modules.chat.agents import invoke_agent
from app.modules.chat.errors import InvalidAgentResponse
from app.modules.chat.prompts.juiz import JUIZ_PROMPT_COMPLETO
from app.modules.chat.prompts.orquestrador import ORQUESTRADOR_PROMPT_COMPLETO
from app.modules.chat.prompts.roteador import ROTEADOR_PROMPT_COMPLETO
from app.modules.chat.schemas import JudgeDecision, InputDecision, MemorySearch, OutputDecision
from app.modules.chat.state import ChatState
from app.modules.chat.subgraphs import (
    _filtros_treinamentos,
    _filtros_eventos,
    _filtros_nrs_organizacao,
    _pedido_conformidade_terceiro,
    _campo_dado_proprio,
    build_agenda_graph,
    build_faq_graph,
    build_rh_graph,
    build_sst_graph,
)
from app.modules.guardrails.entrada import GUARDRAIL_ENTRADA_PROMPT_COMPLETO
from app.modules.guardrails.saida import GUARDRAIL_SAIDA_PROMPT_COMPLETO
from app.modules.roteador.tools import (
    ConsultarAcessosArgs, ConsultarConversasArgs, ConsultarNotificacoesArgs,
    EnviarMensagemArgs, TOOLS_ROTEADOR,
)
from app.modules.shared.tools import gerar_pdf


ROTEADOR_TOOLS = {registered_tool.name: registered_tool for registered_tool in TOOLS_ROTEADOR}
logger = logging.getLogger(__name__)


def _sem_acentos(message: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(char)
    )


def _pedido_de_agendamento_reuniao(message: str) -> bool:
    """Classifica apenas a intenção; não preenche filtros nem autoriza criação."""
    normalized = _sem_acentos(message)
    return bool(re.search(
        r"\b(?:marcar|agendar|criar|organizar)\b.{0,100}\b(?:reuniao|reunioes)\b",
        normalized, re.DOTALL,
    ))


def _pedido_de_historico_ia(message: str) -> str | None:
    """Reconhece memória própria, sem confundir mensagens entre pessoas."""
    normalized = _sem_acentos(message)
    if re.search(r"\b(?:nao|apague|exclua|delete)\b", normalized):
        return None
    if re.search(r"\b(?:outro usuario|outra pessoa|do colega|da colega)\b", normalized):
        return None
    if re.search(r"\b(?:conversas?|mensagens?)\s+com\s+(?!o astro\b|astro\b|voce\b)", normalized):
        return None
    previous = re.search(
        r"\b(?:conversas?|sessoes|sessao)\b", normalized,
    ) and re.search(
        r"\b(?:anterior(?:es)?|ultima(?:s)?|encerrad[ao]s?|finalizad[ao]s?|outras|passad[ao]s?)\b",
        normalized,
    )
    recalling = re.search(r"\b(?:conversamos|falamos)\b", normalized) and re.search(
        r"\b(?:antes|anteriormente|ultima vez)\b", normalized,
    )
    if not (previous or recalling):
        return None
    # Recência não é similaridade: a última sessão vem do Mongo, não do índice.
    if re.search(r"\b(?:ultima|anterior|acabei de|encerrad[ao]|finalizad[ao])\b", normalized):
        return ""
    return message[:1000] if re.search(r"\b(?:sobre|assunto)\b", normalized) else ""


def _pedido_de_publicacao_sst(message: str) -> bool:
    """Encaminha materiais públicos de SST sem depender da classificação do LLM."""
    normalized = _sem_acentos(message)
    if re.search(r"\b(politica interna|norma interna|procedimento interno|do astro|da empresa)\b", normalized):
        return False
    material = re.search(
        r"\b(cartilhas?|manua(?:l|is)|guias?|orienta(?:cao|coes))\b", normalized,
    )
    fundacentro = re.search(r"\bfundacentro\b", normalized)
    sst_topic = re.search(
        r"\b(riscos? (?:psicossociais?|ocupacionais?)|seguranca (?:do|no) trabalho|"
        r"saude (?:do|no) trabalho|sst|epis?|prevencao de acidentes|"
        r"ergonomia|higiene das maos|gerenciamento de riscos|gro|pgr|"
        r"assedio no trabalho)\b", normalized,
    )
    return bool(material and (fundacentro or sst_topic))


def _pergunta_identidade_astro(message: str) -> bool:
    normalized = _sem_acentos(message).strip().rstrip(".!? ")
    return normalized in {
        "qual e a sua funcao", "qual e sua funcao",
        "quem e voce dentro do astro", "quem e voce",
        "voce e o roteador do astro ou o agente do astro",
    }


def _duvida_conexao_google_calendar(message: str) -> bool:
    normalized = _sem_acentos(message)
    asks_how = bool(re.search(r"\b(?:como|o que preciso fazer|como faco)\b", normalized))
    action = bool(re.search(
        r"\b(?:crie|criar|adicione|adicionar|marque|marcar|envie|enviar)\b",
        normalized,
    ))
    return bool(
        re.search(r"\bgoogle (?:calendar|agenda)\b", normalized)
        and re.search(r"\bconect\w*\b", normalized)
        and re.search(r"\b(?:como|preciso|quero|ainda nao|desconectad\w*)\b", normalized)
        and (not action or (asks_how and re.search(r"\bainda nao conect\w*\b", normalized)))
    )


def _pedido_pdf(message: str) -> bool:
    """Distingue pedido de arquivo de uma pergunta genérica sobre PDFs."""
    normalized = _sem_acentos(message)
    if not re.search(r"\bpdfs?\b", normalized):
        return False
    if re.search(r"\b(?:sem\s+pdf|nao\s+(?:quero|preciso|gere|gerar|crie|criar|faca|envie)\b)", normalized):
        return False
    if re.search(r"\bcomo\s+(?:gerar|criar|fazer|exportar)\b", normalized):
        return False
    return bool(re.search(
        r"\b(?:gere|gerar|crie|criar|faca|fazer|exporte|exportar|"
        r"salve|salvar|produza|quero|preciso|gostaria|mande|envie)\b|^pdf\b",
        normalized,
    ))


def _confirmacao_explicita(message: str) -> bool:
    normalized = _sem_acentos(message).strip().rstrip(".!?")
    if normalized in {
        "sim", "confirmo", "isso mesmo", "e isso mesmo", "esta certo",
        "sim, eu confirmo", "sim eu confirmo", "pode mandar assim",
    }:
        return True
    action = r"(?:enviar|mandar|criar|adicionar|agendar|envie|envia|manda|mande|crie|adicione|agende)"
    object_detail = (
        r"(?:\s+(?:a mensagem|o evento|a criacao|o agendamento|isso))?"
        r"(?:\s+no meu google (?:calendar|agenda))?"
    )
    return bool(re.fullmatch(
        rf"(?:sim,?\s+)?(?:eu\s+)?(?:confirmo(?:\s+(?:o envio|a criacao|o agendamento))?|"
        rf"pode\s+{action}{object_detail}|{action}{object_detail}|"
        rf"e isso mesmo que eu quero enviar|esta certo,? pode\s+(?:enviar|mandar))",
        normalized,
    ))


def _cancelamento_explicito(message: str) -> bool:
    normalized = message.casefold().strip().rstrip(".!?")
    return bool(re.fullmatch(
        r"(nao|não|cancelar|cancele|nao envie|não envie|desista|pode cancelar)",
        normalized,
    ))


def _pedido_simples_de_mensagem(message: str) -> EnviarMensagemArgs | None:
    """Prepara pedidos evidentes sem depender do LLM; nunca confirma o envio."""
    normalized = _sem_acentos(message)
    send_verb = re.search(r"\b(manda|mande|mandar|envie|enviar|envia)\b", normalized)
    if send_verb is None:
        return None
    if re.search(r"\b(nao|como|se)\b", normalized[:send_verb.start()]):
        return None
    if re.search(r"\b(melhor[ae]|reescrev\w*|corrig\w*|reformul\w*)\b", normalized):
        return None

    recipient_match = re.search(
        r"\b(?:para|pra|pro)\s+(?:(?:a|o|ao)\s+)?(?P<destinatario>.+?)"
        r"(?=\s*,?\s*por favor\b|\s+e\s+(?:quero|gostaria|vou)\s+(?:enviar|mandar)\b|$)",
        message, re.IGNORECASE,
    )
    if recipient_match is None:
        return None
    recipient = recipient_match.group("destinatario").strip(" \t\r\n.,!?\"'“”")
    # Pronomes e relações não identificam um destinatário sem consulta adicional.
    if _sem_acentos(recipient) in {"ele", "ela", "meu amigo", "minha amiga", "alguem"}:
        return None

    before_recipient = message[:recipient_match.start()]
    quoted = re.search(
        r"\b(?:mensagem|texto|dizendo)\s*[:]?\s*['\"“](?P<texto>[^'\"”]+)['\"”]",
        before_recipient, re.IGNORECASE,
    ) or re.search(r"['\"“](?P<texto>[^'\"”]+)['\"”]", before_recipient)
    if quoted:
        body = quoted.group("texto").strip()
    elif re.search(r"\b(?:um\s+)?oi\b", _sem_acentos(before_recipient)):
        body = "Oi"
    else:
        return None

    try:
        return EnviarMensagemArgs(destinatario=recipient, mensagem=body)
    except ValidationError:
        return None


def _pedido_simples_de_conversa(message: str) -> ConsultarConversasArgs | None:
    """Reconhece consultas explícitas com pessoa identificada sem chamar o LLM."""
    normalized = _sem_acentos(message)
    if re.search(r"\b(?:nao|mande|mandar|envie|enviar|responda)\b", normalized):
        return None
    if not re.search(
        r"\b(?:mostre|mostrar|liste|listar|veja|ver|consulte|consultar|busque|buscar)\b",
        normalized,
    ):
        return None

    page_match = re.search(r"\b(?:na\s+)?p[aá]gina\s+(\d+)\b", message, re.IGNORECASE)
    page = int(page_match.group(1)) if page_match else 1
    without_page = (
        message[:page_match.start()] + message[page_match.end():]
        if page_match else message
    )
    match = re.search(
        r"\b(?:mensagens?|conversas?)\s+com\s+(?:(?:a|o)\s+)?"
        r"(?P<pessoa>[\w@.+\-]+(?:\s+[\w@.+\-]+){0,4})"
        r"\s*(?:,?\s*por favor)?[.!?]?\s*$",
        without_page,
        re.IGNORECASE,
    )
    if match is None:
        return None
    person = match.group("pessoa").strip(" .!?")
    if _sem_acentos(person) in {
        "ele", "ela", "meu amigo", "minha amiga", "meu gestor", "minha gestora",
    }:
        return None
    try:
        return ConsultarConversasArgs(pessoa=person, pagina=page)
    except ValidationError:
        return None


_NUMEROS_PAGINACAO = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4,
    "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10,
}


def _limite_notificacoes(message: str) -> int | None:
    normalized = _sem_acentos(message)
    match = re.search(
        r"\b(?:no maximo|ate|com|mostre|liste)\s+(\d{1,2}|"
        + "|".join(_NUMEROS_PAGINACAO) + r")\b", normalized,
    )
    if match is None:
        return None
    value = match.group(1)
    limit = int(value) if value.isdigit() else _NUMEROS_PAGINACAO[value]
    return limit if 1 <= limit <= 10 else None


def _pedido_simples_de_notificacoes(
    message: str, history: list[dict[str, str]] | None = None,
    last_route: str = "",
) -> ConsultarNotificacoesArgs | None:
    """Consulta própria explícita sem depender da classificação do modelo."""
    normalized = _sem_acentos(message)
    mentions_notifications = bool(re.search(r"\bnotificacoes?\b", normalized))
    next_page = bool(re.search(r"\b(?:proxima|seguinte) pagina\b", normalized))
    if not mentions_notifications and not (next_page and last_route == "notificacoes"):
        return None
    if not next_page and not re.search(
        r"\b(?:mostre|mostrar|liste|listar|veja|ver|consulte|consultar|"
        r"busque|buscar|quais|tenho)\b", normalized,
    ):
        return None
    if re.search(r"\b(?:nao|apague|exclua|marque|crie|envie)\b", normalized):
        return None
    if re.search(r"\bnotificacoes?\s+(?:de|do|da|para)\s+(?!mim\b)\w+", normalized):
        return None
    page_match = re.search(r"\bpagina\s+(\d+)\b", normalized)
    limit = _limite_notificacoes(message)
    requested_limit = limit
    page = int(page_match.group(1)) if page_match else 1
    if next_page and not page_match:
        previous_page = 1
        for turn in history or []:
            if turn.get("role") not in {"human", "user"}:
                continue
            previous = _sem_acentos(turn.get("content", ""))
            if re.search(r"\bnotificacoes?\b", previous):
                explicit = re.search(r"\bpagina\s+(\d+)\b", previous)
                previous_page = int(explicit.group(1)) if explicit else 1
            elif re.search(r"\b(?:proxima|seguinte) pagina\b", previous):
                previous_page += 1
            if re.search(r"\b(?:proxima|seguinte) pagina\b", previous) and not re.search(
                r"\bpagina\s+\d+\b", previous,
            ) and re.search(r"\bnotificacoes?\b", previous):
                previous_page += 1
            limit = _limite_notificacoes(previous) or limit
        page = previous_page + 1
        limit = requested_limit or limit
    try:
        return ConsultarNotificacoesArgs(
            pagina=page, limite=limit or 5,
        )
    except ValidationError:
        return None


def _continuacao_segura_notificacoes(state: ChatState) -> bool:
    if state.get("contexto", {}).get("ultima_rota") != "notificacoes":
        return False
    normalized = _sem_acentos(state["mensagem"]).strip().rstrip(".!? ")
    return normalized in {
        "e a proxima pagina", "a proxima pagina", "mostre a proxima pagina",
        "mostre a proxima pagina das minhas notificacoes",
    }


_MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5,
    "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}


def _pedido_simples_de_acessos(message: str) -> ConsultarAcessosArgs | None:
    """Reconhece perguntas inequívocas sobre os próprios dias de acesso."""
    normalized = _sem_acentos(message)
    if not re.search(r"\b(?:acess\w*|logins?|entrei)\b", normalized):
        return None
    if re.search(
        r"\b(?:nao\s+(?:quero|consulte|mostrar|mostre|liste|listar|buscar|busque)|"
        r"permiss\w*|nivel|liberar|conceder|bloquear)\b", normalized,
    ):
        return None
    if re.search(r"\b(?:de|do|da)\s+(?:outro|outra|usuario|funcionario|colega)\b", normalized):
        return None
    explicar = bool(
        re.search(r"\b(?:por que|porque|explique|explica|como|o que significa)\b", normalized)
        and re.search(
            r"\b(?:contagem|contad[oa]s?|contabiliz\w*|contar|registr\w*|dias|vezes)\b",
            normalized,
        )
    )
    if not explicar and not re.search(
        r"\b(?:meu|meus|minha|minhas|eu|acessei|entrei|tive)\b", normalized,
    ):
        return None

    first = bool(re.search(r"\b(?:primeir\w*\s+(?:acesso|vez)|mais antigo)\b", normalized))
    last = bool(re.search(r"\b(?:ultim\w*\s+(?:acesso|vez)|mais recente)\b", normalized))
    if re.search(r"\b(?:quais dias|liste|listar|mostre os dias)\b", normalized):
        consulta = "dias"
    elif re.search(r"\b(?:quant\w*|vezes|total)\b", normalized):
        consulta = "contagem"
    elif first and last:
        consulta = "resumo"
    elif first:
        consulta = "primeiro"
    elif last:
        consulta = "ultimo"
    elif explicar:
        consulta = "explicacao"
    elif re.search(r"\b(?:resumo|historico|meus acessos)\b", normalized):
        consulta = "resumo"
    else:
        return None

    if consulta == "explicacao":
        return ConsultarAcessosArgs(consulta="explicacao")

    periodo = "todo_historico"
    values = {}
    relative_month = bool(re.search(
        r"\b(?:mes passado|mes anterior|ultimo mes|"
        r"(?:este|esse|neste|nesse) mes|mes atual)\b", normalized,
    ))
    relative_year = bool(re.search(
        r"\b(?:ano passado|ano anterior|ultimo ano|"
        r"(?:este|esse|neste|nesse) ano|ano atual)\b", normalized,
    ))
    explicit_years = re.findall(r"\b(?:19|20)\d{2}\b", normalized)
    explicit_months = re.findall(r"\b(" + "|".join(_MESES) + r")\b", normalized)
    if (relative_month and relative_year) or (
        (relative_month or relative_year) and (explicit_years or explicit_months)
    ):
        return None
    interval = re.search(
        r"\bentre\s+(\d{2}/\d{2}/\d{4})\s+e\s+(\d{2}/\d{2}/\d{4})\b",
        normalized,
    )
    if interval is None and re.search(r"\bentre\b", normalized):
        return None
    if interval:
        try:
            values["data_inicio"] = date.fromisoformat(
                "-".join(reversed(interval.group(1).split("/")))
            )
            values["data_fim"] = date.fromisoformat(
                "-".join(reversed(interval.group(2).split("/")))
            )
        except ValueError:
            return None
        periodo = "intervalo"
    elif re.search(r"\b(?:mes passado|mes anterior|ultimo mes)\b", normalized):
        periodo = "mes_passado"
    elif re.search(r"\b(?:ano passado|ano anterior|ultimo ano)\b", normalized):
        periodo = "ano_passado"
    elif re.search(r"\b(?:este|esse|neste|nesse) mes\b|\bmes atual\b", normalized):
        periodo = "mes_atual"
    elif re.search(r"\b(?:este|esse|neste|nesse) ano\b|\bano atual\b", normalized):
        periodo = "ano_atual"
    else:
        if len(set(explicit_years)) > 1 or len(set(explicit_months)) > 1:
            return None
        year_match = explicit_years[0] if explicit_years else None
        month_match = explicit_months[0] if explicit_months else None
        if month_match and year_match:
            periodo = "mes_especifico"
            values.update(ano=int(year_match), mes=_MESES[month_match])
        elif year_match:
            periodo = "ano_especifico"
            values["ano"] = int(year_match)
        elif month_match or re.search(r"\b(?:mes|ano|periodo|entre)\b", normalized):
            # Sem ano ou intervalo inequívoco, deixe o modelo pedir esclarecimento.
            return None

    page = re.search(r"\bpagina\s+(\d+)\b", normalized)
    try:
        return ConsultarAcessosArgs(
            consulta=consulta, explicar=explicar, periodo=periodo,
            pagina=int(page.group(1)) if page else 1, **values,
        )
    except ValidationError:
        return None


def build_chat_graph(model: AgentModel, search_memory=None, search_faq=None):
    async def input_guard(state: ChatState):
        if (_campo_dado_proprio(state["mensagem"]) is not None
                or _pergunta_identidade_astro(state["mensagem"])
                or _continuacao_segura_notificacoes(state)):
            # Intenção read-only estritamente reconhecida. Identidade e acesso
            # continuam verificados pela autenticação e pela tool de dados próprios.
            return {
                "rota": "roteador", "resposta": "", "guardar_turno": True,
                "pdf_solicitado": False, "agentes_chamados": ["guardrail_entrada"],
            }
        pending = state.get("acao_pendente")
        if (
            isinstance(pending, dict)
            and pending.get("tipo") in {
                "enviar_mensagem", "criar_evento_google_calendar",
            }
            and (
                _confirmacao_explicita(state["mensagem"])
                or _cancelamento_explicito(state["mensagem"])
            )
        ):
            # A mensagem curta é contextualizada por uma ação criada pelo backend.
            # A tool ainda revalida sessão, conteúdo, prazo e confirmação exatos.
            return {
                "rota": "roteador",
                "resposta": "",
                "guardar_turno": False,
                "pdf_solicitado": False,
                "agentes_chamados": ["guardrail_entrada"],
            }
        decision = await invoke_agent(
            model, "guardrail_entrada", GUARDRAIL_ENTRADA_PROMPT_COMPLETO, state, InputDecision,
        )
        approved = decision.decisao == "aprovar"
        return {
            "rota": "roteador" if approved else "fim",
            "resposta": "" if approved else decision.mensagem,
            "guardar_turno": decision.decisao != "bloquear",
            "pdf_solicitado": approved and _pedido_pdf(state["mensagem"]),
            "agentes_chamados": ["guardrail_entrada"],
        }

    async def router(state: ChatState):
        if _pergunta_identidade_astro(state["mensagem"]):
            return {
                "rota": "direta",
                "candidato": (
                    "Sou o Agente do Astro. Posso ajudar com informações de RH, "
                    "segurança do trabalho, agenda, treinamentos, políticas internas "
                    "e notificações, conforme suas permissões."
                ),
                "agentes_chamados": state["agentes_chamados"] + ["roteador"],
            }
        if _campo_dado_proprio(state["mensagem"]) is not None:
            return {"rota": "rh", "agentes_chamados": state["agentes_chamados"] + ["roteador"]}
        memory_request = _pedido_de_historico_ia(state["mensagem"])
        if memory_request is not None and not state.get("memoria_consultada") and search_memory is not None:
            return {
                "rota": "memoria", "busca_memoria": memory_request,
                "agentes_chamados": state["agentes_chamados"] + ["roteador"],
            }
        if _duvida_conexao_google_calendar(state["mensagem"]):
            return {
                "rota": "direta",
                "candidato": (
                    "A conexão do Google Calendar é opcional. Quando quiser usá-lo, "
                    "acesse a rota autenticada GET /integracoes/google-calendar/conectar "
                    "e abra o authorization_url retornado para autorizar sua conta. "
                    "Depois, volte ao chat e faça o pedido novamente. "
                    "Seus eventos e treinamentos internos do Astro continuam disponíveis sem essa conexão."
                ),
                "agentes_chamados": state["agentes_chamados"] + ["roteador"],
            }
        pending = state.get("acao_pendente")
        if isinstance(pending, dict) and pending.get("tipo") == "enviar_mensagem":
            if _cancelamento_explicito(state["mensagem"]):
                return {
                    "rota": "direta",
                    "candidato": "O envio foi cancelado. Nenhuma mensagem foi enviada.",
                    "acao_pendente": None,
                    "agentes_chamados": state["agentes_chamados"] + ["roteador"],
                }
            if _confirmacao_explicita(state["mensagem"]):
                decision = EnviarMensagemArgs(
                    destinatario=pending["destinatario_email"],
                    mensagem=pending["mensagem"],
                    confirmar_envio=True,
                )
                return {
                    "rota": "mensagem",
                    "roteador_decision": decision,
                    "agentes_chamados": state["agentes_chamados"] + ["roteador"],
                }

        if isinstance(pending, dict) and pending.get("tipo") == "criar_evento_google_calendar":
            if _cancelamento_explicito(state["mensagem"]):
                return {
                    "rota": "direta",
                    "candidato": "A criação foi cancelada. Nenhum evento foi adicionado à agenda.",
                    "acao_pendente": None,
                    "agentes_chamados": state["agentes_chamados"] + ["roteador"],
                }
            if _confirmacao_explicita(state["mensagem"]):
                try:
                    filters = CriarEventoGoogleArgs(
                        titulo=pending["titulo"],
                        inicio=datetime.fromisoformat(pending["inicio"]),
                        fim=datetime.fromisoformat(pending["fim"]),
                        descricao=pending.get("descricao"),
                        confirmar=True,
                    )
                    decision = AgendaToolDecision(
                        acao="criar_evento_google_calendar", filtros=filters,
                    )
                except (KeyError, TypeError, ValueError, ValidationError):
                    return {
                        "rota": "direta",
                        "candidato": "A prévia do evento expirou. Prepare o agendamento novamente.",
                        "acao_pendente": None,
                        "agentes_chamados": state["agentes_chamados"] + ["roteador"],
                    }
                return {
                    "rota": "agenda",
                    "agenda_decision": decision,
                    "confirmacao_explicita": True,
                    "agentes_chamados": state["agentes_chamados"] + ["roteador"],
                }

        simple_message = _pedido_simples_de_mensagem(state["mensagem"])
        if simple_message is not None and memory_request is None:
            return {
                "rota": "mensagem",
                "roteador_decision": simple_message,
                "agentes_chamados": state["agentes_chamados"] + ["roteador"],
            }

        simple_conversation = _pedido_simples_de_conversa(state["mensagem"])
        if simple_conversation is not None and memory_request is None:
            return {
                "rota": "conversa",
                "roteador_decision": simple_conversation,
                "agentes_chamados": state["agentes_chamados"] + ["roteador"],
            }

        simple_notifications = _pedido_simples_de_notificacoes(
            state["mensagem"], state["historico"], state["contexto"].get("ultima_rota", ""),
        )
        if simple_notifications is not None and memory_request is None:
            return {
                "rota": "notificacoes",
                "roteador_decision": simple_notifications,
                "agentes_chamados": state["agentes_chamados"] + ["roteador"],
            }

        simple_access = _pedido_simples_de_acessos(state["mensagem"])
        if simple_access is not None and memory_request is None:
            return {
                "rota": "acessos",
                "roteador_decision": simple_access,
                "agentes_chamados": state["agentes_chamados"] + ["roteador"],
            }

        if memory_request is None and (
            _filtros_treinamentos(state["mensagem"]) is not None
            or _filtros_eventos(state["mensagem"]) is not None
            or _pedido_de_agendamento_reuniao(state["mensagem"])
        ):
            return {
                "rota": "agenda",
                "agentes_chamados": state["agentes_chamados"] + ["roteador"],
            }

        if memory_request is None and (_pedido_conformidade_terceiro(state["mensagem"])
                or _pedido_de_publicacao_sst(state["mensagem"])
                or _filtros_nrs_organizacao(state["mensagem"]) is not None):
            return {
                "rota": "sst",
                "agentes_chamados": state["agentes_chamados"] + ["roteador"],
            }

        text = await invoke_agent(model, "roteador", ROTEADOR_PROMPT_COMPLETO, state)
        command = text.strip()
        code_block = re.fullmatch(
            r"```(?:json|text)?\s*(.*?)\s*```", command,
            re.DOTALL | re.IGNORECASE,
        )
        if code_block:
            command = code_block.group(1).strip()
        memory_match = re.fullmatch(r"MEMORY\s*=\s*(\{.*\})", command, re.DOTALL)
        if memory_match:
            if state.get("memoria_consultada") or search_memory is None:
                raise InvalidAgentResponse("roteador")
            try:
                search = MemorySearch.model_validate_json(memory_match.group(1))
            except ValidationError:
                raise InvalidAgentResponse("roteador") from None
            return {"rota": "memoria", "busca_memoria": search.busca,
                    "agentes_chamados": state["agentes_chamados"] + ["roteador"]}
        message_match = re.fullmatch(r"MESSAGE\s*=\s*(\{.*\})", command, re.DOTALL)
        if message_match:
            try:
                decision = EnviarMensagemArgs.model_validate_json(message_match.group(1))
            except ValidationError:
                raise InvalidAgentResponse("roteador") from None
            decision.confirmar_envio = False
            return {
                "rota": "mensagem",
                "roteador_decision": decision,
                "agentes_chamados": state["agentes_chamados"] + ["roteador"],
            }
        conversation_text = command
        if conversation_text.startswith("NOTIFICATIONS"):
            match = re.fullmatch(r"NOTIFICATIONS\s*=\s*(\{.*\})", conversation_text, re.DOTALL)
            if match is None:
                raise InvalidAgentResponse("roteador")
            try:
                decision = ConsultarNotificacoesArgs.model_validate_json(match.group(1))
            except ValidationError:
                raise InvalidAgentResponse("roteador") from None
            return {
                "rota": "notificacoes",
                "roteador_decision": decision,
                "agentes_chamados": state["agentes_chamados"] + ["roteador"],
            }
        if conversation_text.startswith("ACCESSES"):
            match = re.fullmatch(r"ACCESSES\s*=\s*(\{.*\})", conversation_text, re.DOTALL)
            if match is None:
                raise InvalidAgentResponse("roteador")
            try:
                decision = ConsultarAcessosArgs.model_validate_json(match.group(1))
            except ValidationError:
                raise InvalidAgentResponse("roteador") from None
            return {
                "rota": "acessos",
                "roteador_decision": decision,
                "agentes_chamados": state["agentes_chamados"] + ["roteador"],
            }
        if conversation_text.startswith("CONVERSATION"):
            match = re.fullmatch(r"CONVERSATION\s*=\s*(\{.*\})", conversation_text, re.DOTALL)
            if match is None:
                raise InvalidAgentResponse("roteador")
            try:
                decision = ConsultarConversasArgs.model_validate_json(
                    match.group(1)
                )
            except ValidationError:
                raise InvalidAgentResponse("roteador") from None
            return {
                "rota": "conversa",
                "roteador_decision": decision,
                "agentes_chamados": state["agentes_chamados"] + ["roteador"],
            }
        route = re.fullmatch(
            r"ROUTE\s*=\s*(rh|sst|agenda|faq|fora_escopo)", command, re.IGNORECASE,
        )
        if route and route.group(1).lower() == "fora_escopo":
            return {
                "rota": "direta",
                "candidato": (
                    "Esse pedido está fora do escopo do Astro. Posso ajudar com RH, "
                    "segurança do trabalho, agenda e normas internas. Não consigo "
                    "buscar ou criar esse conteúdo com as fontes e ferramentas disponíveis."
                ),
                "resultado": {"status": "fora_escopo"},
                "agentes_chamados": state["agentes_chamados"] + ["roteador"],
            }
        if not route and re.search(
            r"\b(?:ROUTE|MEMORY|MESSAGE|CONVERSATION|NOTIFICATIONS|ACCESSES)\s*=",
            command, re.IGNORECASE,
        ):
            raise InvalidAgentResponse("roteador")
        return {
            "rota": route.group(1).lower() if route else "direta",
            "candidato": "" if route else command,
            "agentes_chamados": state["agentes_chamados"] + ["roteador"],
        }

    async def send_message(state: ChatState):
        decision = state["roteador_decision"]
        result = await ROTEADOR_TOOLS["enviar_mensagem"].ainvoke(
            decision.model_dump(),
            config={"configurable": {
                "usuario_atual": state["usuario_atual"].model_dump(),
                "session_id": state["session_id"],
                "acao_pendente": state.get("acao_pendente"),
                "confirmacao_explicita": (
                    decision.confirmar_envio and _confirmacao_explicita(state["mensagem"])
                ),
            }},
        )
        status = result.get("status")
        if status == "aguardando_confirmacao":
            draft = result["rascunho"]
            recipient = draft["destinatario"]
            candidate = (
                f"Prévia para {recipient['nome']} ({recipient['email']}):\n\n"
                f"{draft['mensagem']}\n\nConfirma o envio?"
            )
            pending = result["acao_pendente"]
        elif status == "ambiguo":
            options = "\n".join(
                f"- {item['nome']} — {item['email']}"
                for item in result.get("destinatarios", [])
            )
            candidate = f"{result['mensagem']}\n{options}" if options else result["mensagem"]
            pending = None
        elif status == "ok":
            recipient = result["destinatario"]
            candidate = f"Mensagem enviada para {recipient['nome']} ({recipient['email']})."
            pending = None
        else:
            candidate = result.get("mensagem", "Não foi possível processar o envio.")
            pending = state.get("acao_pendente") if status == "indisponivel" else None

        public_result = {
            key: value for key, value in result.items() if key != "acao_pendente"
        }
        return {
            "resultado_tool": public_result,
            "resultado": {
                "dominio": "roteador",
                "intencao": "enviar_mensagem",
                "status": status,
                "evidencia_tool": {
                    "nome": "enviar_mensagem",
                    "resultado": public_result,
                },
            },
            "candidato": candidate,
            "acao_pendente": pending,
            "agentes_chamados": state["agentes_chamados"] + ["enviar_mensagem"],
        }

    async def consult_conversation(state: ChatState):
        decision = state["roteador_decision"]
        result = await ROTEADOR_TOOLS["consultar_conversas"].ainvoke(
            decision.model_dump(),
            config={"configurable": {
                "usuario_atual": state["usuario_atual"].model_dump(),
            }},
        )
        status = result.get("status")
        if status == "ambiguo":
            options = "\n".join(
                f"- {item['nome']} — {item['email']}"
                for item in result.get("pessoas", [])
            )
            candidate = f"{result['mensagem']}\n{options}" if options else result["mensagem"]
        elif status == "sem_dados":
            person = result["pessoa"]
            candidate = f"Não encontrei mensagens trocadas com {person['nome']} ({person['email']})."
        elif status == "ok":
            person = result["pessoa"]
            lines = [f"Mensagens com {person['nome']} ({person['email']}):"]
            for message in result["mensagens"]:
                label = "Você" if message["direcao"] == "enviada" else person["nome"]
                suffix = " [trecho]" if message["trecho"] else ""
                lines.append(f"- {message['data']} — {label}: {message['mensagem']}{suffix}")
            if not result["mensagens"]:
                lines.append("Nenhuma mensagem nesta página. Tente uma página anterior.")
            elif result["pagina"] < result["total_paginas"]:
                lines.append(f"Para ver mensagens anteriores, peça a página {result['pagina'] + 1}.")
            candidate = "\n".join(lines)
        else:
            candidate = result.get("mensagem", "Não foi possível consultar as conversas.")
        return {
            "resultado_tool": result,
            "resultado": {
                "dominio": "roteador",
                "intencao": "consultar_conversas",
                "status": status,
                "evidencia_tool": {"nome": "consultar_conversas", "resultado": result},
            },
            "candidato": candidate,
            "agentes_chamados": state["agentes_chamados"] + ["consultar_conversas"],
        }

    async def consult_notifications(state: ChatState):
        decision = state["roteador_decision"]
        result = await ROTEADOR_TOOLS["consultar_notificacoes"].ainvoke(
            decision.model_dump(),
            config={"configurable": {
                "usuario_atual": state["usuario_atual"].model_dump(),
            }},
        )
        status = result.get("status")
        if status == "sem_dados":
            candidate = "Você não tem notificações cadastradas."
        elif status == "ok":
            lines = ["Suas notificações:"]
            for notification in result["notificacoes"]:
                suffix = " [trecho]" if notification["trecho"] else ""
                lines.append(
                    f"- {notification['data_criacao']} — {notification['mensagem']}{suffix}"
                )
            if not result["notificacoes"]:
                lines.append("Nenhuma notificação nesta página. Tente uma página anterior.")
            elif result["pagina"] < result["total_paginas"]:
                lines.append(f"Para ver notificações anteriores, peça a página {result['pagina'] + 1}.")
            candidate = "\n".join(lines)
        else:
            candidate = result.get("mensagem", "Não foi possível consultar as notificações.")
        return {
            "resultado_tool": result,
            "resultado": {
                "dominio": "roteador",
                "intencao": "consultar_notificacoes",
                "status": status,
                "evidencia_tool": {"nome": "consultar_notificacoes", "resultado": result},
            },
            "candidato": candidate,
            "agentes_chamados": state["agentes_chamados"] + ["consultar_notificacoes"],
        }

    async def consult_accesses(state: ChatState):
        decision = state["roteador_decision"]
        result = await ROTEADOR_TOOLS["consultar_acessos"].ainvoke(
            decision.model_dump(),
            config={"configurable": {
                "usuario_atual": state["usuario_atual"].model_dump(),
            }},
        )
        status = result.get("status")
        explanation = (
            "A tabela registra no máximo uma data de acesso por usuário a cada dia. "
            "Por isso, a contagem representa dias com acesso, não cada login; "
            "ela também não informa horários."
        )
        if status == "ok" and result.get("consulta") == "explicacao":
            candidate = explanation
        elif status in {"ok", "sem_dados"}:
            labels = {
                "todo_historico": "em todo o histórico",
                "mes_atual": "neste mês",
                "ano_atual": "neste ano",
                "mes_passado": "no mês passado",
                "ano_passado": "no ano passado",
            }
            period = result["periodo"]
            if period == "mes_especifico":
                label = f"em {result['mes']:02d}/{result['ano']}"
            elif period == "ano_especifico":
                label = f"em {result['ano']}"
            elif period == "intervalo":
                label = f"entre {result['data_inicio']} e {result['data_fim']}"
            else:
                label = labels[period]
            if status == "sem_dados":
                candidate = f"Não encontrei dias de acesso registrados {label}."
            elif result["consulta"] == "primeiro":
                candidate = (
                    f"Seu primeiro dia de acesso registrado {label} foi "
                    f"{result['primeiro_dia_registrado']}."
                )
            elif result["consulta"] == "ultimo":
                candidate = (
                    f"Seu último dia de acesso registrado {label} foi "
                    f"{result['ultimo_dia_registrado']}."
                )
            elif result["consulta"] == "dias":
                lines = [f"Dias com acesso registrado {label} (mais recentes primeiro):"]
                lines.extend(f"- {day}" for day in result["dias"])
                if not result["dias"]:
                    lines.append("Não há dias nesta página. Tente uma página anterior.")
                elif result["pagina"] < result["total_paginas"]:
                    lines.append(f"Para ver mais dias, peça a página {result['pagina'] + 1}.")
                candidate = "\n".join(lines)
            else:
                count = result["total_dias_com_acesso"]
                candidate = f"Há registros de acesso em {count} dia(s) {label}."
                if result["consulta"] == "resumo":
                    candidate += (
                        f" O primeiro dia foi {result['primeiro_dia_registrado']} "
                        f"e o último, {result['ultimo_dia_registrado']}."
                    )
            if result.get("explicar"):
                candidate += f" {explanation}"
        else:
            candidate = result.get("mensagem", "Não foi possível consultar os acessos.")
        return {
            "resultado_tool": result,
            "resultado": {
                "dominio": "roteador",
                "intencao": "consultar_acessos",
                "status": status,
                "evidencia_tool": {"nome": "consultar_acessos", "resultado": result},
            },
            "candidato": candidate,
            "agentes_chamados": state["agentes_chamados"] + ["consultar_acessos"],
        }

    async def memory_lookup(state: ChatState):
        memory = await search_memory(
            state["usuario_atual"].uid, state["session_id"], state["busca_memoria"],
        )
        return {"memoria": memory, "memoria_consultada": True,
                "agentes_chamados": state["agentes_chamados"] + ["buscar_historico"]}

    async def orchestrator(state: ChatState):
        result = state.get("resultado") or {}
        if (
            result.get("dominio") == "agenda" and result.get("status") == "esclarecer"
            and isinstance(result.get("esclarecer"), str) and result["esclarecer"].strip()
        ):
            # Uma pergunta validada não precisa de outra geração que invente
            # disponibilidade, convites ou permissões. Juiz e saída ainda revisam.
            answer = result["resposta"]
            question = result["esclarecer"]
            return {
                "candidato": answer if question in answer else answer + "\n\n" + question,
                "agentes_chamados": state["agentes_chamados"] + ["orquestrador"],
            }
        text = await invoke_agent(model, "orquestrador", ORQUESTRADOR_PROMPT_COMPLETO, state)
        return {"candidato": text, "agentes_chamados": state["agentes_chamados"] + ["orquestrador"]}

    async def judge(state: ChatState):
        decision = await invoke_agent(
            model, "juiz", JUIZ_PROMPT_COMPLETO, state, JudgeDecision,
        )
        return {
            "avaliacao_juiz": decision.model_dump(),
            "agentes_chamados": state["agentes_chamados"] + ["juiz"],
        }

    async def output_guard(state: ChatState):
        evidence = state.get("resultado", {}).get("evidencia_tool", {})
        if (
            state["avaliacao_juiz"]["status"] == "aprovado"
            and evidence.get("nome") in {
                "buscar_outros_usuarios", "buscar_meus_dados", "consultar_nrs",
                "consultar_nrs_obrigatorias", "consultar_situacao_nrs",
                "consultar_nrs_organizacao",
                "consultar_conformidade_usuario",
                "consultar_orientacoes_sst",
                "enviar_mensagem", "consultar_conversas", "consultar_notificacoes",
                "consultar_acessos",
                "consultar_treinamentos",
                "consultar_eventos",
                "consultar_google_calendar", "criar_evento_google_calendar",
            }
        ):
            return {
                "resposta": state["candidato"],
                "guardar_turno": True,
                "agentes_chamados": state["agentes_chamados"] + ["guardrail_saida"],
            }
        decision = await invoke_agent(
            model, "guardrail_saida", GUARDRAIL_SAIDA_PROMPT_COMPLETO, state, OutputDecision,
        )
        judge_status = state["avaliacao_juiz"]["status"]
        if judge_status != "aprovado" and (
            decision.status == "aprovado"
            or (decision.status == "corrigido" and decision.resposta == state["candidato"])
        ):
            logger.warning(
                "Guardrail de saida nao corrigiu resposta apontada pelo juiz; "
                "juiz=%s guardrail=%s",
                judge_status, decision.status,
            )
            result = {
                "resposta": "Não consegui validar esta resposta com as informações disponíveis.",
                "guardar_turno": False,
                "agentes_chamados": state["agentes_chamados"] + ["guardrail_saida"],
            }
            if evidence.get("nome") in {
                "enviar_mensagem", "criar_evento_google_calendar",
            }:
                result["acao_pendente"] = None
            return result
        # Se o Juiz aprovou e o guardrail também marcou aprovado, o texto
        # autorizado é a candidata original. Ignorar paráfrases evita que uma
        # alteração acidental do modelo vire erro 502 ou introduza fatos novos.
        if decision.status == "aprovado" and decision.resposta != state["candidato"]:
            logger.warning("Guardrail de saida marcou aprovado mas alterou o texto; preservando candidata")
        response = (
            state["candidato"]
            if judge_status == "aprovado" and decision.status == "aprovado"
            else decision.resposta
        )
        result = {
            "resposta": response,
            "guardar_turno": decision.status != "bloqueado",
            "agentes_chamados": state["agentes_chamados"] + ["guardrail_saida"],
        }
        if evidence.get("nome") in {
            "enviar_mensagem", "criar_evento_google_calendar",
        }:
            # Uma prévia reprovada não pode permanecer disponível para confirmação.
            result["acao_pendente"] = None
        return result

    async def generate_pdf(state: ChatState):
        if state.get("resultado", {}).get("status") in {
            "indisponivel", "sem_dados", "nao_autorizado", "esclarecer",
            "aguardando_confirmacao", "erro", "bloqueado",
        }:
            return {
                "resposta": state["resposta"] + (
                    "\n\nNão gerei o PDF porque a consulta não trouxe informações confirmadas."
                ),
                "agentes_chamados": state["agentes_chamados"],
            }
        try:
            result = await gerar_pdf.ainvoke(
                {
                    "titulo": f"Consulta Astro: {state['mensagem'][:110]}",
                    "pergunta": state["mensagem"],
                    "resposta": state["resposta"],
                },
                config={"configurable": {
                    "usuario_atual": state["usuario_atual"].model_dump(),
                }},
            )
        except Exception:
            result = {"status": "indisponivel"}
        if result.get("status") == "ok":
            return {
                "pdf_url": result["url"],
                "agentes_chamados": state["agentes_chamados"] + ["gerar_pdf"],
            }
        return {
            "resposta": state["resposta"] + "\n\nNão consegui gerar o PDF agora.",
            "agentes_chamados": state["agentes_chamados"] + ["gerar_pdf"],
        }

    graph = StateGraph(ChatState)
    graph.add_node("guardrail_entrada", input_guard)
    graph.add_node("roteador", router)
    graph.add_node("buscar_historico", memory_lookup)
    graph.add_node("enviar_mensagem", send_message)
    graph.add_node("consultar_conversas", consult_conversation)
    graph.add_node("consultar_notificacoes", consult_notifications)
    graph.add_node("consultar_acessos", consult_accesses)
    graph.add_edge("buscar_historico", "roteador")
    graph.add_node("rh", build_rh_graph(model))
    graph.add_conditional_edges(
        "rh",
        lambda state: "juiz" if state.get("candidato") else "orquestrador",
        {"juiz": "juiz", "orquestrador": "orquestrador"},
    )
    graph.add_node("sst", build_sst_graph(model))
    graph.add_conditional_edges(
        "sst",
        lambda state: "juiz" if state.get("candidato") else "orquestrador",
        {"juiz": "juiz", "orquestrador": "orquestrador"},
    )
    graph.add_node("agenda", build_agenda_graph(model))
    graph.add_conditional_edges(
        "agenda",
        lambda state: "juiz" if state.get("candidato") else "orquestrador",
        {"juiz": "juiz", "orquestrador": "orquestrador"},
    )
    graph.add_node("faq", build_faq_graph(model, search_faq))
    graph.add_node("orquestrador", orchestrator)
    graph.add_node("juiz", judge)
    graph.add_node("guardrail_saida", output_guard)
    graph.add_node("gerar_pdf", generate_pdf)
    graph.add_edge(START, "guardrail_entrada")
    graph.add_conditional_edges("guardrail_entrada", lambda state: state["rota"], {
        "roteador": "roteador", "fim": END,
    })
    graph.add_conditional_edges("roteador", lambda state: state["rota"], {
        "rh": "rh", "sst": "sst", "agenda": "agenda",
        "faq": "faq", "direta": "juiz",
        "memoria": "buscar_historico", "mensagem": "enviar_mensagem",
        "conversa": "consultar_conversas",
        "notificacoes": "consultar_notificacoes",
        "acessos": "consultar_acessos",
    })
    graph.add_edge("enviar_mensagem", "juiz")
    graph.add_edge("consultar_conversas", "juiz")
    graph.add_edge("consultar_notificacoes", "juiz")
    graph.add_edge("consultar_acessos", "juiz")
    graph.add_edge("faq", "juiz")
    graph.add_edge("orquestrador", "juiz")
    graph.add_edge("juiz", "guardrail_saida")
    graph.add_conditional_edges(
        "guardrail_saida",
        lambda state: "gerar_pdf" if (
            state.get("pdf_solicitado")
            and state.get("guardar_turno")
            and state.get("avaliacao_juiz", {}).get("status") == "aprovado"
            and state.get("rota") in {
                "rh", "sst", "agenda", "eventos", "faq", "conversa",
                "notificacoes", "acessos",
            }
        ) else "fim",
        {"gerar_pdf": "gerar_pdf", "fim": END},
    )
    graph.add_edge("gerar_pdf", END)
    # Sem checkpoints do grafo: o serviço persiste somente turnos públicos no Mongo.
    return graph.compile(name="astro_chat")
