from typing import TypedDict

from app.core.security import CurrentUser


class ChatState(TypedDict, total=False):
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
    candidato: str
    avaliacao_juiz: dict
    resposta: str
    agentes_chamados: list[str]
    guardar_turno: bool
