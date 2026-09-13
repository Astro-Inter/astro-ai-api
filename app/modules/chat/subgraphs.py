import re
import unicodedata

from langgraph.graph import END, START, StateGraph

from app.infrastructure.llm.models import AgentModel
from app.modules.chat.agents import invoke_agent
from app.modules.chat.errors import InvalidAgentResponse
from app.modules.chat.prompts.agenda import AGENDA_PROMPT_COMPLETO
from app.modules.chat.prompts.eventos import EVENTOS_PROMPT_COMPLETO
from app.modules.chat.prompts.faq import FAQ_PROMPT_COMPLETO
from app.modules.chat.prompts.rh import RH_DECISAO_PROMPT_COMPLETO, RH_PROMPT_COMPLETO
from app.modules.chat.prompts.sst import SST_DECISAO_PROMPT_COMPLETO, SST_PROMPT_COMPLETO
from app.modules.chat.schemas import SpecialistResult
from app.modules.chat.state import ChatState
from app.modules.eventos.tools import (
    ConsultarTreinamentosArgs, EventosToolDecision, TOOLS_EVENTOS,
)
from app.modules.rh.tools import BuscarOutrosUsuariosArgs, RhToolDecision, TOOLS_RH
from app.modules.sst.tools import ConsultarNrsArgs, SstToolDecision, TOOLS_SST


SPECIALIST_PROMPTS = {
    "rh": RH_PROMPT_COMPLETO, "sst": SST_PROMPT_COMPLETO, "agenda": AGENDA_PROMPT_COMPLETO,
}
RH_TOOLS = {registered_tool.name: registered_tool for registered_tool in TOOLS_RH}
SST_TOOLS = {registered_tool.name: registered_tool for registered_tool in TOOLS_SST}
EVENTOS_TOOLS = {registered_tool.name: registered_tool for registered_tool in TOOLS_EVENTOS}


def _pedido_dos_proprios_dados(message: str) -> bool:
    normalized = "".join(
        character for character in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(character)
    )
    return bool(re.search(
        r"\b(meus dados|meus dados pessoais|meus dados profissionais|"
        r"minhas informacoes|meu cadastro|meu perfil profissional)\b",
        normalized,
    ))


def _pedido_de_todos_os_usuarios(message: str) -> bool:
    normalized = "".join(
        character for character in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(character)
    )
    return bool(re.search(
        r"\b(todos os usuarios|todos usuarios|todos os funcionarios|"
        r"todas as pessoas do (?:meu|nosso) sistema)\b",
        normalized,
    ))


def _filtros_deterministicos_nrs(message: str) -> ConsultarNrsArgs | None:
    """Reconhece consultas objetivas de NR sem depender de um provedor de IA."""
    normalized = "".join(
        character for character in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(character)
    )
    if not re.search(r"\bnrs?\b|\bnormas? regulamentadoras?\b", normalized):
        return None

    numbers = [int(value) for value in re.findall(r"\bnr\s*-?\s*(\d{1,2})\b", normalized)]
    grouped = re.search(
        r"\bnrs?\s*[-:]?\s*((?:\d{1,2}(?:\s*(?:,|e)\s*\d{1,2})+))",
        normalized,
    )
    if grouped:
        numbers.extend(int(value) for value in re.findall(r"\d{1,2}", grouped.group(1)))
    numbers = list(dict.fromkeys(numbers))
    if len(numbers) > 10:
        return None

    page_match = re.search(r"\bpagina\s+(\d{1,3})\b", normalized)
    page = int(page_match.group(1)) if page_match else 1
    field_markers = {
        "objetivo": ("objetiv", "finalidade"),
        "descricao": ("descri", "conteudo", "explica", "detalh"),
        "aplicabilidade": ("aplicab", "aplica"),
        "revogada": ("revog", "vigent", "vigencia"),
        "tempo_reciclagem_meses": ("reciclagem", "reciclar"),
        "ultima_atualizacao": ("ultima atualizacao", "atualizada", "atualizacao"),
        "data_criacao": ("data de criacao", "criada", "criacao"),
        "usabilidade": ("usabilidade", "publico", "quem pode usar"),
    }
    fields = [
        field for field, markers in field_markers.items()
        if any(marker in normalized for marker in markers)
    ]

    if numbers:
        mode = "detalhar" if len(numbers) == 1 or fields else "listar"
        return ConsultarNrsArgs(
            numeros=numbers,
            campos=fields,
            modo=mode,
            pagina=page,
            limite=50,
        )

    listing = re.search(
        r"\b(quais|liste|listar|listagem|todas|todos)\b", normalized,
    )
    status_query = any(marker in normalized for marker in ("revog", "vigent", "vigencia"))
    if not listing and not status_query:
        return None
    revoked = None
    if any(marker in normalized for marker in ("vigent", "vigencia", "nao revogad")):
        revoked = False
    elif "revog" in normalized:
        revoked = True
    return ConsultarNrsArgs(
        revogada=revoked,
        modo="listar",
        pagina=page,
        limite=50,
    )


def _pedido_nrs_obrigatorias(message: str) -> bool:
    normalized = "".join(
        character for character in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(character)
    )
    mentions_nr = bool(re.search(r"\bnrs?\b|\bnormas? regulamentadoras?\b", normalized))
    obligation = bool(re.search(
        r"\b(obrigatori\w*|preciso cumprir|devo cumprir|aplicaveis? (?:ao|a) meu|"
        r"exigid[ao]s?|para (?:o )?meu cargo|para (?:a )?minha funcao)\b",
        normalized,
    ))
    return mentions_nr and obligation


def _pedido_situacao_nrs(message: str) -> bool:
    normalized = "".join(
        character for character in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(character)
    )
    mentions_nr = bool(re.search(r"\bnrs?\b|\bnormas? regulamentadoras?\b", normalized))
    personal_context = bool(re.search(
        r"\b(minhas?|meus?|para mim|eu preciso|preciso|devo|tenho que)\b",
        normalized,
    ))
    situation = bool(re.search(
        r"\b(situacao|status|valid[ao]s?|validade|em dia|pendent\w*|vencid\w*|"
        r"realizar|renovar|renovacao|reciclagem)\b",
        normalized,
    ))
    return mentions_nr and personal_context and situation


def _filtros_treinamentos(message: str) -> ConsultarTreinamentosArgs | None:
    normalized = "".join(
        character for character in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(character)
    )
    if not re.search(r"\b(treinamentos?|capacitac(?:ao|oes))\b", normalized):
        return None
    if not re.search(
        r"\b(meus?|minhas?|preciso|tenho|devo|fazer|realizar|"
        r"pendentes?|atribuidos?|inscritos?|conclui|concluidos?)\b", normalized,
    ):
        return None
    if re.search(r"\b(conclui|concluidos?|ja fiz|finalizei)\b", normalized):
        situation = "concluidos"
    elif re.search(r"\b(todos|historico)\b", normalized) and not re.search(
        r"\b(preciso|pendentes?|realizar|fazer)\b", normalized,
    ):
        situation = "todos"
    else:
        situation = "a_realizar"
    page_match = re.search(r"\bpagina\s+(\d+)\b", normalized)
    try:
        return ConsultarTreinamentosArgs(
            situacao=situation,
            pagina=int(page_match.group(1)) if page_match else 1,
        )
    except ValueError:
        return None


def _formatar_usuarios(result: dict) -> str:
    if result.get("status") == "sem_dados":
        return "Não encontrei usuários com os filtros informados."
    if result.get("status") != "ok":
        return result.get("mensagem", "Não foi possível consultar os usuários no momento.")

    users = result["usuarios"]
    title = f"Encontrei {len(users)} usuário(s):"
    lines = []
    for user in users:
        details = [
            user["cargo"], user["unidade"], user.get("modalidade"),
            user["tipo"], user["status"], user["email"],
        ]
        lines.append(f"- {user['nome']}: " + " | ".join(str(item) for item in details if item))
    return "\n".join([title, *lines])


def _formatar_meus_dados(result: dict) -> str:
    if result.get("status") == "sem_dados":
        return "Não encontrei seus dados cadastrais."
    if result.get("status") != "ok":
        return result.get("mensagem", "Não foi possível consultar seus dados no momento.")

    data = result["dados"]
    labels = {
        "nome": "Nome", "email": "E-mail", "cpf": "CPF", "tipo": "Perfil",
        "cargo": "Cargo", "unidade": "Unidade", "modalidade": "Modalidade",
        "status": "Status", "criado_em": "Cadastrado em",
    }
    lines = [f"- {labels[field]}: {value}" for field, value in data.items() if value is not None]
    return "\n".join(["Estes são os seus dados:", *lines])


def _formatar_nrs(result: dict) -> str:
    if result.get("status") == "sem_dados":
        return "Não encontrei NRs com os filtros informados."
    if result.get("status") != "ok":
        return result.get("mensagem", "Não foi possível consultar as NRs no momento.")

    pagination = result.get("paginacao", {})
    if result.get("modo") == "listar":
        total = pagination.get("total", result.get("quantidade", 0))
        page = pagination.get("pagina", 1)
        total_pages = pagination.get("total_paginas", 1)
        lines = []
        for nr in result["nrs"]:
            details = [nr.get("situacao")]
            if nr.get("ultima_atualizacao"):
                details.append(f"atualizada em {nr['ultima_atualizacao']}")
            suffix = f" | {' | '.join(details)}" if any(details) else ""
            lines.append(f"- NR-{nr.get('numero')} — {nr.get('nome', 'Sem nome')}{suffix}")
        footer = ["Fonte: MongoDB `nrs`."]
        if pagination.get("tem_proxima_pagina"):
            footer.insert(0, f"Há mais resultados. Solicite a página {page + 1}.")
        return "\n".join([
            f"Encontrei {total} NR(s). Página {page} de {total_pages}:",
            *lines,
            *footer,
        ])

    labels = {
        "nome": "Nome", "objetivo": "Objetivo", "descricao": "Descrição",
        "aplicabilidade": "Aplicabilidade", "revogada": "Revogada",
        "tempo_reciclagem_meses": "Reciclagem (meses)",
        "ultima_atualizacao": "Última atualização", "data_criacao": "Criada em",
        "usabilidade": "Usabilidade",
    }
    sections = []
    for nr in result["nrs"]:
        number = nr.get("numero")
        title = f"NR-{number}"
        if nr.get("nome"):
            title += f" — {nr['nome']}"
        details = []
        for field, label in labels.items():
            if field == "nome" or field not in nr:
                continue
            value = nr[field]
            if value is None:
                continue
            if field == "revogada":
                value = "Sim" if value else "Não"
            details.append(f"- {label}: {value}")
        details.append(f"- Fonte: MongoDB `nrs`, documento NR-{number}")
        sections.append("\n".join([title, *details]))
    return "\n\n".join(sections)


def _evidencia_compacta_nrs(result: dict) -> dict:
    """Evita repetir no prompt do Juiz os textos já presentes na candidata."""
    return {
        "status": result.get("status"),
        "quantidade": result.get("quantidade", 0),
        "modo": result.get("modo"),
        "numeros": [nr.get("numero") for nr in result.get("nrs", [])],
        "campos_retornados": sorted({
            field
            for nr in result.get("nrs", [])
            for field in nr
            if field != "numero"
        }),
        "fonte": result.get("fonte"),
        "paginacao": result.get("paginacao"),
        "formatacao": "resposta gerada deterministicamente a partir do resultado da tool",
    }


def _formatar_nrs_obrigatorias(result: dict) -> str:
    if result.get("status") == "nao_aplicavel":
        return result["mensagem"]
    if result.get("status") == "sem_dados":
        return result.get("mensagem", "Não encontrei seu cadastro funcional.")
    if result.get("status") != "ok":
        return result.get("mensagem", "Não foi possível consultar suas NRs obrigatórias.")

    nrs = result["nrs"]
    header = (
        f"Para o cargo {result['cargo']}, na unidade {result['unidade']}, "
        f"encontrei {len(nrs)} NR(s) vigente(s) vinculada(s):"
    )
    if not nrs:
        return "\n".join([
            header,
            "Nenhuma NR vigente está vinculada ao seu cargo.",
        ])
    lines = []
    for nr in nrs:
        recycling = nr.get("tempo_reciclagem_meses")
        suffix = f" | reciclagem: {recycling} meses" if recycling is not None else ""
        lines.append(f"- NR-{nr['numero']} — {nr['titulo']}{suffix}")
    return "\n".join([
        header,
        *lines,
    ])


def _evidencia_nrs_obrigatorias(result: dict) -> dict:
    return {
        "status": result.get("status"),
        "cargo": result.get("cargo"),
        "unidade": result.get("unidade"),
        "quantidade": result.get("quantidade", 0),
        "nrs": result.get("nrs", []),
        "fonte": result.get("fonte"),
        "formatacao": "resposta gerada deterministicamente a partir do resultado da tool",
    }


def _formatar_situacao_nrs(result: dict) -> str:
    if result.get("status") == "nao_aplicavel":
        return result["mensagem"]
    if result.get("status") == "sem_dados":
        return result.get("mensagem", "Não encontrei seu cadastro funcional.")
    if result.get("status") != "ok":
        return result.get("mensagem", "Não foi possível consultar a situação das suas NRs.")

    nrs = result["nrs"]
    header = (
        f"Situação das NRs obrigatórias para o cargo {result['cargo']}, "
        f"na unidade {result['unidade']}:"
    )
    if not nrs:
        return "\n".join([header, "Nenhuma NR vigente está vinculada ao seu cargo."])

    situation_labels = {
        "VIGENTE": "vigente",
        "PENDENTE": "pendente",
        "RENOVACAO_NECESSARIA": "renovação necessária",
        "REALIZACAO_NECESSARIA": "realização necessária",
    }
    action_labels = {
        "NENHUMA": "nenhuma ação imediata",
        "CONCLUIR_PENDENCIA": "concluir atividade pendente",
        "RENOVAR_EM_BREVE": "renovar em breve",
        "RENOVAR": "renovar",
        "REALIZAR": "realizar",
    }
    lines = []
    for nr in nrs:
        details = [
            f"situação: {situation_labels.get(nr['situacao'], nr['situacao'].lower())}",
            f"ação: {action_labels.get(nr['acao_necessaria'], nr['acao_necessaria'].lower())}",
        ]
        if nr.get("data_validade"):
            details.append(f"validade: {nr['data_validade']}")
        if nr.get("data_inicio_pendencia"):
            details.append(f"atividade prevista: {nr['data_inicio_pendencia']}")
        lines.append(f"- NR-{nr['numero']} — {nr['titulo']} | " + " | ".join(details))
    return "\n".join([header, *lines])


def _formatar_treinamentos(result: dict) -> str:
    if result.get("status") == "sem_dados":
        labels = {
            "a_realizar": "Não encontrei treinamentos ativos pendentes atribuídos a você.",
            "concluidos": "Não encontrei treinamentos concluídos atribuídos a você.",
            "todos": "Não encontrei treinamentos atribuídos a você.",
        }
        return labels.get(result.get("situacao"), "Não encontrei treinamentos.")
    if result.get("status") != "ok":
        return result.get("mensagem", "Não foi possível consultar seus treinamentos.")

    lines = ["Seus treinamentos atribuídos:"]
    status_labels = {
        "PENDENTE": "pendente", "REJEITADO": "rejeitado", "CONCLUIDO": "concluído",
    }
    for training in result["treinamentos"]:
        details = [
            f"turma: {training['turma']}",
            f"início: {training['data_inicio']}",
            f"término: {training['data_termino']}",
            f"participação: {status_labels.get(training['status_participacao'], training['status_participacao'])}",
            f"evento: {training['status_evento'].lower()}",
        ]
        if training.get("nr"):
            details.append(f"NR-{training['nr']['numero']} — {training['nr']['titulo']}")
        details.append(f"conclusão por: {training['modo_conclusao'].lower().replace('_', ' ')}")
        if training["evidencia_obrigatoria"]:
            details.append("evidência obrigatória")
        if training.get("data_validade"):
            details.append(f"validade: {training['data_validade']}")
        lines.append(f"- {training['titulo']} | " + " | ".join(details))
        if training.get("descricao"):
            lines.append(f"  {training['descricao'][:160]}")
        if training.get("motivo_rejeicao"):
            lines.append(f"  Motivo da rejeição: {training['motivo_rejeicao'][:160]}")
        if training.get("link_externo") and len(training["link_externo"]) <= 300:
            lines.append(f"  Link: {training['link_externo']}")
    if not result["treinamentos"]:
        lines.append("Nenhum treinamento nesta página. Tente uma página anterior.")
    elif result["pagina"] < result["total_paginas"]:
        lines.append(f"Para ver mais treinamentos, peça a página {result['pagina'] + 1}.")
    return "\n".join(lines)


def _evidencia_treinamentos(result: dict) -> dict:
    """Envia ao Juiz só os campos que podem aparecer na resposta formatada."""
    visible_fields = (
        "titulo", "turma", "data_inicio", "data_termino", "nr",
        "status_evento", "status_participacao", "modo_conclusao",
        "evidencia_obrigatoria", "data_validade",
    )
    trainings = []
    for training in result.get("treinamentos", []):
        item = {field: training.get(field) for field in visible_fields}
        if training.get("descricao"):
            item["descricao"] = training["descricao"][:160]
        if training.get("motivo_rejeicao"):
            item["motivo_rejeicao"] = training["motivo_rejeicao"][:160]
        if training.get("link_externo") and len(training["link_externo"]) <= 300:
            item["link_externo"] = training["link_externo"]
        trainings.append(item)
    return {
        "status": result.get("status"),
        "situacao": result.get("situacao"),
        "pagina": result.get("pagina"),
        "total_paginas": result.get("total_paginas"),
        "treinamentos": trainings,
        "formatacao": "resposta gerada deterministicamente a partir do resultado da tool",
    }


def _evidencia_situacao_nrs(result: dict) -> dict:
    return {
        "status": result.get("status"),
        "cargo": result.get("cargo"),
        "unidade": result.get("unidade"),
        "data_referencia": result.get("data_referencia"),
        "quantidade": result.get("quantidade", 0),
        "nrs": result.get("nrs", []),
        "fonte": result.get("fonte"),
        "formatacao": "resposta gerada deterministicamente a partir do resultado da tool",
    }


def build_specialist_graph(domain: str, model: AgentModel):
    async def specialist(state: ChatState):
        result = await invoke_agent(
            model, domain, SPECIALIST_PROMPTS[domain], state, SpecialistResult,
        )
        if result.dominio != domain:
            raise InvalidAgentResponse(domain)
        return {
            "resultado": result.model_dump(exclude_none=True),
            "agentes_chamados": state["agentes_chamados"] + [domain],
        }

    graph = StateGraph(ChatState)
    graph.add_node("especialista", specialist)
    graph.add_edge(START, "especialista")
    graph.add_edge("especialista", END)
    return graph.compile(name=f"subgrafo_{domain}")


def build_rh_graph(model: AgentModel):
    async def decide(state: ChatState):
        if _pedido_dos_proprios_dados(state["mensagem"]):
            return {
                "rh_route": "tool",
                "rh_decision": RhToolDecision(acao="buscar_meus_dados"),
                "agentes_chamados": state["agentes_chamados"] + ["rh"],
            }
        if _pedido_de_todos_os_usuarios(state["mensagem"]):
            return {
                "rh_route": "tool",
                "rh_decision": RhToolDecision(
                    acao="buscar_outros_usuarios",
                    filtros=BuscarOutrosUsuariosArgs(limite=50),
                ),
                "agentes_chamados": state["agentes_chamados"] + ["rh"],
            }
        decision = await invoke_agent(
            model, "rh", RH_DECISAO_PROMPT_COMPLETO, state, RhToolDecision,
        )
        if decision.acao == "responder":
            return {
                "rh_route": "fim",
                "resultado": decision.resposta.model_dump(exclude_none=True),
                "agentes_chamados": state["agentes_chamados"] + ["rh"],
            }
        return {
            "rh_route": "tool",
            "rh_decision": decision,
            "agentes_chamados": state["agentes_chamados"] + ["rh"],
        }

    async def use_tool(state: ChatState):
        decision = state["rh_decision"]
        tool_name = decision.acao
        tool_input = (
            decision.filtros.model_dump()
            if tool_name == "buscar_outros_usuarios" and decision.filtros is not None
            else {}
        )
        result = await RH_TOOLS[tool_name].ainvoke(
            tool_input,
            config={"configurable": {"usuario_atual": state["usuario_atual"].model_dump()}},
        )
        tool_status = result.get("status")
        statuses = {
            "ok": "concluido",
            "sem_dados": "sem_dados",
            "indisponivel": "indisponivel",
            "erro": "nao_autorizado",
            "nao_autorizado": "nao_autorizado",
        }
        if tool_name == "buscar_meus_dados":
            messages = {
                "ok": "Consulta dos dados do usuário autenticado concluída.",
                "sem_dados": "O usuário autenticado não foi encontrado no cadastro.",
                "indisponivel": "Não foi possível consultar seus dados no momento.",
                "erro": "Não foi possível identificar o usuário autenticado.",
            }
        else:
            messages = {
                "ok": f"Consulta concluída com {result.get('quantidade', 0)} usuário(s).",
                "sem_dados": "Nenhum usuário foi encontrado com os filtros informados.",
                "indisponivel": "Não foi possível consultar os usuários no momento.",
                "erro": "Não foi possível identificar o usuário autorizado para a consulta.",
                "nao_autorizado": result.get(
                    "mensagem", "Seu perfil não permite consultar outros usuários."
                ),
            }
        specialist_result = {
            "dominio": "rh",
            "intencao": "consultar",
            "status": statuses.get(tool_status, "indisponivel"),
            "resposta": messages.get(
                tool_status, "Não foi possível confirmar o resultado da consulta.",
            ),
            "recomendacao": "",
            "evidencia_tool": {
                "nome": tool_name,
                "resultado": result,
            },
        }
        return {
            "resultado_tool": result,
            "resultado": specialist_result,
            "candidato": (
                _formatar_meus_dados(result)
                if tool_name == "buscar_meus_dados"
                else _formatar_usuarios(result)
            ),
            "agentes_chamados": state["agentes_chamados"] + [tool_name],
        }

    graph = StateGraph(ChatState)
    graph.add_node("decidir", decide)
    graph.add_node("usar_tool_rh", use_tool)
    graph.add_edge(START, "decidir")
    graph.add_conditional_edges("decidir", lambda state: state["rh_route"], {
        "tool": "usar_tool_rh", "fim": END,
    })
    graph.add_edge("usar_tool_rh", END)
    return graph.compile(name="subgrafo_rh")


def build_sst_graph(model: AgentModel):
    async def decide(state: ChatState):
        if _pedido_situacao_nrs(state["mensagem"]):
            return {
                "sst_route": "tool",
                "sst_decision": SstToolDecision(acao="consultar_situacao_nrs"),
                "agentes_chamados": state["agentes_chamados"] + ["sst"],
            }
        if _pedido_nrs_obrigatorias(state["mensagem"]):
            return {
                "sst_route": "tool",
                "sst_decision": SstToolDecision(acao="consultar_nrs_obrigatorias"),
                "agentes_chamados": state["agentes_chamados"] + ["sst"],
            }
        deterministic_filters = _filtros_deterministicos_nrs(state["mensagem"])
        if deterministic_filters is not None:
            return {
                "sst_route": "tool",
                "sst_decision": SstToolDecision(
                    acao="consultar_nrs", filtros=deterministic_filters,
                ),
                "agentes_chamados": state["agentes_chamados"] + ["sst"],
            }
        decision = await invoke_agent(
            model, "sst", SST_DECISAO_PROMPT_COMPLETO, state, SstToolDecision,
        )
        if decision.acao == "responder":
            return {
                "sst_route": "fim",
                "resultado": decision.resposta.model_dump(exclude_none=True),
                "agentes_chamados": state["agentes_chamados"] + ["sst"],
            }
        return {
            "sst_route": "tool",
            "sst_decision": decision,
            "agentes_chamados": state["agentes_chamados"] + ["sst"],
        }

    async def use_tool(state: ChatState):
        decision = state["sst_decision"]
        tool_name = decision.acao
        tool_input = decision.filtros.model_dump() if decision.filtros is not None else {}
        result = await SST_TOOLS[tool_name].ainvoke(
            tool_input,
            config={"configurable": {
                "usuario_atual": state["usuario_atual"].model_dump(),
            }},
        )
        tool_status = result.get("status")
        statuses = {
            "ok": "concluido",
            "sem_dados": "sem_dados",
            "indisponivel": "indisponivel",
            "erro": "nao_autorizado",
            "nao_aplicavel": "nao_autorizado",
        }
        messages = {
            "ok": f"Consulta concluída com {result.get('quantidade', 0)} NR(s).",
            "sem_dados": "Nenhuma NR foi encontrada com os filtros informados.",
            "indisponivel": "Não foi possível consultar as NRs no momento.",
            "erro": "Não foi possível identificar o usuário autenticado.",
            "nao_aplicavel": result.get(
                "mensagem", "A consulta não se aplica ao perfil autenticado.",
            ),
        }
        specialist_result = {
            "dominio": "sst",
            "intencao": "consultar",
            "status": statuses.get(tool_status, "indisponivel"),
            "resposta": messages.get(
                tool_status, "Não foi possível confirmar o resultado da consulta.",
            ),
            "recomendacao": "",
            "fontes": [{
                "titulo": "Normas Regulamentadoras",
                "referencia": (
                    "MongoDB: collection nrs"
                    if tool_name == "consultar_nrs"
                    else "PostgreSQL: registros funcionais e de conformidade"
                ),
            }],
            "evidencia_tool": {"nome": tool_name, "resultado": (
                _evidencia_situacao_nrs(result)
                if tool_name == "consultar_situacao_nrs"
                else _evidencia_nrs_obrigatorias(result)
                if tool_name == "consultar_nrs_obrigatorias"
                else _evidencia_compacta_nrs(result)
            )},
        }
        return {
            "resultado_tool": result,
            "resultado": specialist_result,
            "candidato": (
                _formatar_situacao_nrs(result)
                if tool_name == "consultar_situacao_nrs"
                else _formatar_nrs_obrigatorias(result)
                if tool_name == "consultar_nrs_obrigatorias"
                else _formatar_nrs(result)
            ),
            "agentes_chamados": state["agentes_chamados"] + [tool_name],
        }

    graph = StateGraph(ChatState)
    graph.add_node("decidir", decide)
    graph.add_node("usar_tool_sst", use_tool)
    graph.add_edge(START, "decidir")
    graph.add_conditional_edges("decidir", lambda state: state["sst_route"], {
        "tool": "usar_tool_sst", "fim": END,
    })
    graph.add_edge("usar_tool_sst", END)
    return graph.compile(name="subgrafo_sst")


def build_eventos_graph(model: AgentModel):
    async def decide(state: ChatState):
        filters = _filtros_treinamentos(state["mensagem"])
        if filters is not None:
            decision = EventosToolDecision(acao="consultar_treinamentos", filtros=filters)
        else:
            decision = await invoke_agent(
                model, "eventos", EVENTOS_PROMPT_COMPLETO, state, EventosToolDecision,
            )
        if decision.acao == "responder":
            return {
                "eventos_route": "fim",
                "resultado": decision.resposta.model_dump(exclude_none=True),
                "agentes_chamados": state["agentes_chamados"] + ["eventos"],
            }
        return {
            "eventos_route": "tool",
            "eventos_decision": decision,
            "agentes_chamados": state["agentes_chamados"] + ["eventos"],
        }

    async def use_tool(state: ChatState):
        filters = state["eventos_decision"].filtros
        result = await EVENTOS_TOOLS["consultar_treinamentos"].ainvoke(
            filters.model_dump(),
            config={"configurable": {
                "usuario_atual": state["usuario_atual"].model_dump(),
            }},
        )
        tool_status = result.get("status")
        statuses = {
            "ok": "concluido", "sem_dados": "sem_dados", "sem_perfil": "sem_dados",
            "indisponivel": "indisponivel", "erro": "nao_autorizado",
            "nao_aplicavel": "nao_autorizado",
        }
        return {
            "resultado_tool": result,
            "resultado": {
                "dominio": "eventos",
                "intencao": "consultar",
                "status": statuses.get(tool_status, "indisponivel"),
                "resposta": "Consulta dos treinamentos atribuídos ao usuário autenticado.",
                "recomendacao": "",
                "evidencia_tool": {
                    "nome": "consultar_treinamentos",
                    "resultado": _evidencia_treinamentos(result),
                },
            },
            "candidato": _formatar_treinamentos(result),
            "agentes_chamados": state["agentes_chamados"] + ["consultar_treinamentos"],
        }

    graph = StateGraph(ChatState)
    graph.add_node("decidir", decide)
    graph.add_node("usar_tool_eventos", use_tool)
    graph.add_edge(START, "decidir")
    graph.add_conditional_edges("decidir", lambda state: state["eventos_route"], {
        "tool": "usar_tool_eventos", "fim": END,
    })
    graph.add_edge("usar_tool_eventos", END)
    return graph.compile(name="subgrafo_eventos")


def build_faq_graph(model: AgentModel, search_faq=None):
    async def consult_norms(state: ChatState):
        if search_faq is None:
            result = {"dominio": "faq", "status": "indisponivel", "trechos": []}
        else:
            snippets = await search_faq(state["mensagem"])
            result = {
                "dominio": "faq",
                "status": "encontrado" if snippets else "sem_dados",
                "trechos": snippets,
            }
        return {
            "resultado": result,
            "agentes_chamados": state["agentes_chamados"] + ["consultar_normas"],
        }

    async def answer(state: ChatState):
        status = state["resultado"]["status"]
        if status == "indisponivel":
            response = ("A consulta às normas está indisponível no momento. "
                        "Confirme sua dúvida com a área responsável.")
        elif status == "sem_dados":
            response = "Não encontrei essa informação nas normas disponibilizadas ao Astro."
        else:
            response = await invoke_agent(model, "faq", FAQ_PROMPT_COMPLETO, state)
        return {
            "candidato": response,
            "agentes_chamados": state["agentes_chamados"] + ["faq"],
        }

    graph = StateGraph(ChatState)
    graph.add_node("consultar_normas", consult_norms)
    graph.add_node("responder", answer)
    graph.add_edge(START, "consultar_normas")
    graph.add_edge("consultar_normas", "responder")
    graph.add_edge("responder", END)
    return graph.compile(name="subgrafo_faq")
