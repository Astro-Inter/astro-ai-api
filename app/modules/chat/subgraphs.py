from langgraph.graph import END, START, StateGraph

from app.infrastructure.llm.models import AgentModel
from app.modules.chat.agents import invoke_agent
from app.modules.chat.errors import InvalidAgentResponse
from app.modules.chat.prompts.agenda import AGENDA_PROMPT_COMPLETO
from app.modules.chat.prompts.faq import FAQ_PROMPT_COMPLETO
from app.modules.chat.prompts.rh import RH_DECISAO_PROMPT_COMPLETO, RH_PROMPT_COMPLETO
from app.modules.chat.prompts.sst import SST_PROMPT_COMPLETO
from app.modules.chat.schemas import SpecialistResult
from app.modules.chat.state import ChatState
from app.modules.rh.tools import RhToolDecision, TOOLS_RH


SPECIALIST_PROMPTS = {
    "rh": RH_PROMPT_COMPLETO, "sst": SST_PROMPT_COMPLETO, "agenda": AGENDA_PROMPT_COMPLETO,
}
RH_TOOLS = {registered_tool.name: registered_tool for registered_tool in TOOLS_RH}


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
        tool_input = decision.filtros.model_dump() if decision.filtros is not None else {}
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
            "candidato": _formatar_usuarios(result),
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
