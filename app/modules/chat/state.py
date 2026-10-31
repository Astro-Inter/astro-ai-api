from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from app.core.security import CurrentUser


class ChatState(TypedDict, total=False):
    # Conversa curta do LangGraph, sincronizada com o Mongo a cada turno.
    messages: Annotated[list[AnyMessage], add_messages]
    usuario_atual: CurrentUser
    session_id: str
    memoria: dict
    busca_memoria: str
    memoria_consultada: bool
    # Histórico público já revisado, nunca respostas intermediárias dos agentes.
    historico: list[dict[str, str]]
    mensagem: str
    contexto: dict
    rota: str
    resultado: dict
    resultado_tool: dict
    rh_decision: object
    rh_route: str
    sst_decision: object
    sst_route: str
    agenda_decision: object
    agenda_route: str
    operational_decision: dict | None
    roteador_decision: object
    acao_pendente: dict | None
    candidato: str
    avaliacao_juiz: dict
    resposta: str
    agentes_chamados: list[str]
    guardar_turno: bool
    pdf_solicitado: bool
    pdf_url: str | None
    resposta_deterministica: bool
    consulta_rh: dict | None
    ultimo_pdf: dict | None
