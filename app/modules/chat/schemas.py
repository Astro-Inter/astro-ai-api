from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=4000)
    session_id: UUID | None = None


class ChatResponse(BaseModel):
    session_id: UUID
    resposta: str
    agentes_chamados: list[str]


class SessionResponse(BaseModel):
    session_id: UUID
    status: Literal["ativa", "encerrada"]
    resumo: str | None = None
    resumo_indexado: bool = False


class MemorySearch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    busca: str = Field(max_length=1000)


class InputDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisao: Literal["aprovar", "bloquear", "esclarecer"]
    motivo: Literal[
        "legitimo", "injecao_de_prompt", "acesso_nao_autorizado", "pedido_danoso",
        "fraude", "assedio", "contexto_insuficiente",
    ]
    mensagem: str = Field(max_length=6000)

    @model_validator(mode="after")
    def validate_message(self):
        if self.decisao == "aprovar" and self.mensagem:
            raise ValueError("Aprovacao deve ter mensagem vazia.")
        if self.decisao != "aprovar" and not self.mensagem.strip():
            raise ValueError("Decisao exige mensagem.")
        return self


class OutputDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Literal["aprovado", "corrigido", "bloqueado"]
    motivo: str = Field(min_length=1, max_length=1000)
    resposta: str = Field(min_length=1, max_length=6000)


class SpecialistResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dominio: Literal["rh", "sst", "agenda"]
    intencao: Literal[
        "consultar", "orientar", "solicitar", "atualizar", "registrar", "criar",
        "cancelar", "listar", "disponibilidade", "conflitos",
    ]
    status: Literal[
        "concluido", "esclarecer", "aguardando_confirmacao", "sem_dados",
        "indisponivel", "nao_autorizado",
    ]
    resposta: str = Field(min_length=1, max_length=6000)
    recomendacao: str = Field(max_length=2000)
    esclarecer: str | None = Field(default=None, max_length=1000)
    urgencia: Literal["imediata"] | None = None
    # Sem ferramentas nesta etapa: resultados não podem afirmar escrita ou citar
    # fontes inventadas. Esses contratos serão estendidos com as integrações reais.

    @model_validator(mode="after")
    def validate_result(self):
        if self.status in {"esclarecer", "aguardando_confirmacao"} and not self.esclarecer:
            raise ValueError("Falta pergunta de esclarecimento ou confirmacao.")
        if self.status == "concluido" and self.intencao != "orientar":
            raise ValueError("Operacoes e consultas exigem ferramentas reais.")
        return self
