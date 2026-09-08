from typing import TypedDict


class ChatState(TypedDict, total=False):
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
    resposta: str
    agentes_chamados: list[str]
    guardar_turno: bool
