import re
import unicodedata

from pydantic import ValidationError

from langgraph.graph import END, START, StateGraph

from app.infrastructure.llm.models import AgentModel
from app.modules.chat.agents import invoke_agent
from app.modules.chat.errors import InvalidAgentResponse
from app.modules.chat.prompts.juiz import JUIZ_PROMPT_COMPLETO
from app.modules.chat.prompts.orquestrador import ORQUESTRADOR_PROMPT_COMPLETO
from app.modules.chat.prompts.roteador import ROTEADOR_PROMPT_COMPLETO
from app.modules.chat.schemas import JudgeDecision, InputDecision, MemorySearch, OutputDecision
from app.modules.chat.state import ChatState
from app.modules.chat.subgraphs import (
    build_faq_graph,
    build_rh_graph,
    build_specialist_graph,
    build_sst_graph,
)
from app.modules.guardrails.entrada import GUARDRAIL_ENTRADA_PROMPT_COMPLETO
from app.modules.guardrails.saida import GUARDRAIL_SAIDA_PROMPT_COMPLETO
from app.modules.roteador.tools import ConsultarConversasArgs, EnviarMensagemArgs, TOOLS_ROTEADOR


ROTEADOR_TOOLS = {registered_tool.name: registered_tool for registered_tool in TOOLS_ROTEADOR}


def _sem_acentos(message: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(char)
    )


def _confirmacao_explicita(message: str) -> bool:
    normalized = _sem_acentos(message).strip().rstrip(".!?")
    return bool(re.fullmatch(
        r"(sim(?:,? (?:pode (?:enviar|mandar)|envie|manda))?|"
        r"confirmo(?: o envio)?|pode (?:enviar|mandar)|envie|envia|manda|mande|"
        r"e isso mesmo(?: que eu quero enviar|,? pode (?:enviar|mandar))?|isso mesmo|"
        r"esta certo(?:,? pode (?:enviar|mandar))?|pode mandar assim)",
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


def build_chat_graph(model: AgentModel, search_memory=None, search_faq=None):
    async def input_guard(state: ChatState):
        decision = await invoke_agent(
            model, "guardrail_entrada", GUARDRAIL_ENTRADA_PROMPT_COMPLETO, state, InputDecision,
        )
        approved = decision.decisao == "aprovar"
        return {
            "rota": "roteador" if approved else "fim",
            "resposta": "" if approved else decision.mensagem,
            "guardar_turno": decision.decisao != "bloquear",
            "agentes_chamados": ["guardrail_entrada"],
        }

    async def router(state: ChatState):
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

        simple_message = _pedido_simples_de_mensagem(state["mensagem"])
        if simple_message is not None:
            return {
                "rota": "mensagem",
                "roteador_decision": simple_message,
                "agentes_chamados": state["agentes_chamados"] + ["roteador"],
            }

        simple_conversation = _pedido_simples_de_conversa(state["mensagem"])
        if simple_conversation is not None:
            return {
                "rota": "conversa",
                "roteador_decision": simple_conversation,
                "agentes_chamados": state["agentes_chamados"] + ["roteador"],
            }

        text = await invoke_agent(model, "roteador", ROTEADOR_PROMPT_COMPLETO, state)
        if text.startswith("MEMORY="):
            if state.get("memoria_consultada") or search_memory is None:
                raise InvalidAgentResponse("roteador")
            try:
                search = MemorySearch.model_validate_json(text[len("MEMORY="):])
            except ValidationError:
                raise InvalidAgentResponse("roteador") from None
            return {"rota": "memoria", "busca_memoria": search.busca,
                    "agentes_chamados": state["agentes_chamados"] + ["roteador"]}
        if text.startswith("MESSAGE="):
            try:
                decision = EnviarMensagemArgs.model_validate_json(text[len("MESSAGE="):])
            except ValidationError:
                raise InvalidAgentResponse("roteador") from None
            decision.confirmar_envio = False
            return {
                "rota": "mensagem",
                "roteador_decision": decision,
                "agentes_chamados": state["agentes_chamados"] + ["roteador"],
            }
        conversation_text = text.strip()
        code_block = re.fullmatch(
            r"```(?:json)?\s*(.*?)\s*```", conversation_text,
            re.DOTALL | re.IGNORECASE,
        )
        if code_block:
            conversation_text = code_block.group(1).strip()
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
        route = re.fullmatch(r"ROUTE=(rh|sst|agenda|faq)", text)
        if not route and any(
            marker in text.upper() for marker in ("ROUTE", "MEMORY=", "MESSAGE=", "CONVERSATION=")
        ):
            raise InvalidAgentResponse("roteador")
        return {
            "rota": route.group(1) if route else "direta",
            "candidato": "" if route else text,
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

    async def memory_lookup(state: ChatState):
        memory = await search_memory(
            state["usuario_atual"].uid, state["session_id"], state["busca_memoria"],
        )
        return {"memoria": memory, "memoria_consultada": True,
                "agentes_chamados": state["agentes_chamados"] + ["buscar_historico"]}

    async def orchestrator(state: ChatState):
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
                "enviar_mensagem", "consultar_conversas",
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
        if judge_status != "aprovado" and decision.status == "aprovado":
            raise InvalidAgentResponse("guardrail_saida")
        if (judge_status != "aprovado" and decision.status == "corrigido"
                and decision.resposta == state["candidato"]):
            raise InvalidAgentResponse("guardrail_saida")
        # "Aprovado" não autoriza o revisor a introduzir novas informações.
        if decision.status == "aprovado" and decision.resposta != state["candidato"]:
            raise InvalidAgentResponse("guardrail_saida")
        result = {
            "resposta": decision.resposta,
            "guardar_turno": decision.status != "bloqueado",
            "agentes_chamados": state["agentes_chamados"] + ["guardrail_saida"],
        }
        if evidence.get("nome") == "enviar_mensagem":
            # Uma prévia reprovada não pode permanecer disponível para confirmação.
            result["acao_pendente"] = None
        return result

    graph = StateGraph(ChatState)
    graph.add_node("guardrail_entrada", input_guard)
    graph.add_node("roteador", router)
    graph.add_node("buscar_historico", memory_lookup)
    graph.add_node("enviar_mensagem", send_message)
    graph.add_node("consultar_conversas", consult_conversation)
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
    graph.add_node("agenda", build_specialist_graph("agenda", model))
    graph.add_edge("agenda", "orquestrador")
    graph.add_node("faq", build_faq_graph(model, search_faq))
    graph.add_node("orquestrador", orchestrator)
    graph.add_node("juiz", judge)
    graph.add_node("guardrail_saida", output_guard)
    graph.add_edge(START, "guardrail_entrada")
    graph.add_conditional_edges("guardrail_entrada", lambda state: state["rota"], {
        "roteador": "roteador", "fim": END,
    })
    graph.add_conditional_edges("roteador", lambda state: state["rota"], {
        "rh": "rh", "sst": "sst", "agenda": "agenda", "faq": "faq", "direta": "juiz",
        "memoria": "buscar_historico", "mensagem": "enviar_mensagem",
        "conversa": "consultar_conversas",
    })
    graph.add_edge("enviar_mensagem", "juiz")
    graph.add_edge("consultar_conversas", "juiz")
    graph.add_edge("faq", "juiz")
    graph.add_edge("orquestrador", "juiz")
    graph.add_edge("juiz", "guardrail_saida")
    graph.add_edge("guardrail_saida", END)
    # Sem checkpoints do grafo: o serviço persiste somente turnos públicos no Mongo.
    return graph.compile(name="astro_chat")
