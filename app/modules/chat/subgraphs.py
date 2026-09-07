from langgraph.graph import END, START, StateGraph

from app.infrastructure.llm.models import AgentModel
from app.modules.chat.agents import invoke_agent
from app.modules.chat.errors import InvalidAgentResponse
from app.modules.chat.prompts.agenda import AGENDA_PROMPT_COMPLETO
from app.modules.chat.prompts.rh import RH_PROMPT_COMPLETO
from app.modules.chat.prompts.sst import SST_PROMPT_COMPLETO
from app.modules.chat.schemas import SpecialistResult
from app.modules.chat.state import ChatState


SPECIALIST_PROMPTS = {
    "rh": RH_PROMPT_COMPLETO, "sst": SST_PROMPT_COMPLETO, "agenda": AGENDA_PROMPT_COMPLETO,
}


def build_specialist_graph(domain: str, model: AgentModel):
    async def specialist(state: ChatState):
        result = await invoke_agent(
            model, domain, SPECIALIST_PROMPTS[domain], state, SpecialistResult,
        )
        if result.dominio != domain:
            raise InvalidAgentResponse()
        return {
            "resultado": result.model_dump(exclude_none=True),
            "agentes_chamados": state["agentes_chamados"] + [domain],
        }

    graph = StateGraph(ChatState)
    graph.add_node("especialista", specialist)
    graph.add_edge(START, "especialista")
    graph.add_edge("especialista", END)
    return graph.compile(name=f"subgrafo_{domain}")


def build_faq_graph():
    async def consult_norms(state: ChatState):
        # Ponto de integração da consulta autorizada: sem acervo/retriever, não há
        # evidências para invocar o prompt FAQ e responder sobre normas da empresa.
        return {
            "resultado": {"dominio": "faq", "status": "indisponivel", "fontes": []},
            "agentes_chamados": state["agentes_chamados"] + ["faq"],
        }

    async def answer(state: ChatState):
        return {"resposta": "A consulta às normas está indisponível no momento. "
                            "Confirme sua dúvida com a área responsável."}

    graph = StateGraph(ChatState)
    graph.add_node("consultar_normas", consult_norms)
    graph.add_node("responder", answer)
    graph.add_edge(START, "consultar_normas")
    graph.add_edge("consultar_normas", "responder")
    graph.add_edge("responder", END)
    return graph.compile(name="subgrafo_faq")
