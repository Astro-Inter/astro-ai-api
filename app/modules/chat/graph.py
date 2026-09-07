import re

from langgraph.graph import END, START, StateGraph

from app.infrastructure.llm.models import AgentModel
from app.modules.chat.agents import invoke_agent
from app.modules.chat.errors import InvalidAgentResponse
from app.modules.chat.prompts.orquestrador import ORQUESTRADOR_PROMPT_COMPLETO
from app.modules.chat.prompts.roteador import ROTEADOR_PROMPT_COMPLETO
from app.modules.chat.schemas import InputDecision, OutputDecision
from app.modules.chat.state import ChatState
from app.modules.chat.subgraphs import build_faq_graph, build_specialist_graph
from app.modules.guardrails.entrada import GUARDRAIL_ENTRADA_PROMPT_COMPLETO
from app.modules.guardrails.saida import GUARDRAIL_SAIDA_PROMPT_COMPLETO


def build_chat_graph(model: AgentModel):
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
        text = await invoke_agent(model, "roteador", ROTEADOR_PROMPT_COMPLETO, state)
        route = re.fullmatch(r"ROUTE=(rh|sst|agenda|faq)", text)
        if not route and "ROUTE" in text.upper():
            raise InvalidAgentResponse()
        return {
            "rota": route.group(1) if route else "direta",
            "candidato": "" if route else text,
            "agentes_chamados": state["agentes_chamados"] + ["roteador"],
        }

    async def orchestrator(state: ChatState):
        text = await invoke_agent(model, "orquestrador", ORQUESTRADOR_PROMPT_COMPLETO, state)
        return {"candidato": text, "agentes_chamados": state["agentes_chamados"] + ["orquestrador"]}

    async def output_guard(state: ChatState):
        decision = await invoke_agent(
            model, "guardrail_saida", GUARDRAIL_SAIDA_PROMPT_COMPLETO, state, OutputDecision,
        )
        # "Aprovado" não autoriza o revisor a introduzir novas informações.
        if decision.status == "aprovado" and decision.resposta != state["candidato"]:
            raise InvalidAgentResponse()
        return {
            "resposta": decision.resposta,
            "guardar_turno": decision.status != "bloqueado",
            "agentes_chamados": state["agentes_chamados"] + ["guardrail_saida"],
        }

    graph = StateGraph(ChatState)
    graph.add_node("guardrail_entrada", input_guard)
    graph.add_node("roteador", router)
    for domain in ("rh", "sst", "agenda"):
        graph.add_node(domain, build_specialist_graph(domain, model))
        graph.add_edge(domain, "orquestrador")
    graph.add_node("faq", build_faq_graph())
    graph.add_node("orquestrador", orchestrator)
    graph.add_node("guardrail_saida", output_guard)
    graph.add_edge(START, "guardrail_entrada")
    graph.add_conditional_edges("guardrail_entrada", lambda state: state["rota"], {
        "roteador": "roteador", "fim": END,
    })
    graph.add_conditional_edges("roteador", lambda state: state["rota"], {
        "rh": "rh", "sst": "sst", "agenda": "agenda", "faq": "faq", "direta": "guardrail_saida",
    })
    graph.add_edge("faq", END)
    graph.add_edge("orquestrador", "guardrail_saida")
    graph.add_edge("guardrail_saida", END)
    # Sem checkpoints intermediários: apenas turnos públicos completos ficam no serviço.
    return graph.compile(name="astro_chat")
