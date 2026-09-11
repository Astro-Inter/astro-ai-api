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


class JudgeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Literal["aprovado", "revisar", "rejeitado"]
    motivo: str = Field(min_length=1, max_length=1000)
    problemas: list[str] = Field(max_length=8)

    @model_validator(mode="after")
    def validate_problems(self):
        if any(not problem or len(problem) > 500 for problem in self.problemas):
            raise ValueError("Problema invalido.")
        if self.status == "aprovado" and self.problemas:
            raise ValueError("Aprovacao nao deve listar problemas.")
        if self.status != "aprovado" and not self.problemas:
            raise ValueError("Revisao ou rejeicao exige problemas.")
        return self


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
    # RH já pode concluir consultas por meio de sua tool somente de leitura.
    # Os demais domínios continuam sem autorização para afirmar operações.

    @model_validator(mode="after")
    def validate_result(self):
        if self.status in {"esclarecer", "aguardando_confirmacao"} and not self.esclarecer:
            raise ValueError("Falta pergunta de esclarecimento ou confirmacao.")
        consulta_rh = self.dominio == "rh" and self.intencao == "consultar"
        if self.status == "concluido" and self.intencao != "orientar" and not consulta_rh:
            raise ValueError("Operacoes e consultas exigem ferramentas reais.")
        return self
