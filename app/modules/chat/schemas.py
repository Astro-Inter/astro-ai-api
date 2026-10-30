from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=4000, examples=["NRs para minha unidade"])
    session_id: UUID | None = Field(
        default=None, description="Omita para criar uma conversa; envie o UUID para continuar uma sessão ativa.",
    )


class BlocoTexto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: Literal["texto"] = "texto"
    conteudo: str = Field(min_length=1, max_length=6000)


class PontoGrafico(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categoria: str = Field(min_length=1, max_length=100)
    valor: int = Field(ge=0)


class BlocoGrafico(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: Literal["grafico_barras"] = "grafico_barras"
    versao: Literal[1] = 1
    titulo: str = Field(min_length=1, max_length=200)
    dados: list[PontoGrafico] = Field(min_length=1, max_length=12)
    unidade: Literal["participações"] = "participações"
    fonte: str = Field(min_length=1, max_length=200)


class BlocoGraficoSetores(BlocoGrafico):
    tipo: Literal["grafico_setores"] = "grafico_setores"


class BlocoGraficoLinhas(BlocoGrafico):
    tipo: Literal["grafico_linhas"] = "grafico_linhas"


class ColunaTabela(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chave: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    titulo: str = Field(min_length=1, max_length=100)


class BlocoTabela(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: Literal["tabela"] = "tabela"
    versao: Literal[1] = 1
    titulo: str = Field(min_length=1, max_length=200)
    colunas: list[ColunaTabela] = Field(min_length=1, max_length=10)
    linhas: list[dict[str, str | int | float | None]] = Field(min_length=1, max_length=50)
    pagina: int = Field(ge=1)
    total_paginas: int = Field(ge=1)
    total: int = Field(ge=0)

    @model_validator(mode="after")
    def validar_colunas(self):
        keys = [column.chave for column in self.colunas]
        if len(keys) != len(set(keys)) or any(set(row) != set(keys) for row in self.linhas):
            raise ValueError("Linhas e colunas da tabela devem corresponder.")
        if any(isinstance(value, str) and len(value) > 500 for row in self.linhas for value in row.values()):
            raise ValueError("Conteúdo de tabela excede o limite.")
        return self


class ImagemUsuario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nome: str = Field(min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    url: str | None = Field(default=None, max_length=4096)


class BlocoGaleriaUsuarios(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: Literal["galeria_usuarios"] = "galeria_usuarios"
    versao: Literal[1] = 1
    titulo: str = Field(min_length=1, max_length=200)
    imagens: list[ImagemUsuario] = Field(min_length=1, max_length=50)


BlocoResposta = Annotated[BlocoTexto | BlocoGrafico | BlocoGraficoSetores | BlocoGraficoLinhas | BlocoTabela | BlocoGaleriaUsuarios, Field(discriminator="tipo")]


class ChatResponse(BaseModel):
    session_id: UUID
    resposta: str
    agentes_chamados: list[str]
    blocos: list[BlocoResposta] = Field(default_factory=list, max_length=10)


class SessionResponse(BaseModel):
    session_id: UUID
    status: Literal["ativa", "encerrada"]
    resumo: str | None = None
    resumo_indexado: bool = False
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"session_id": "94229143-d20b-4766-80a4-05341435c236", "status": "ativa",
         "resumo": None, "resumo_indexado": False},
        {"session_id": "94229143-d20b-4766-80a4-05341435c236", "status": "encerrada",
         "resumo": "O usuário consultou as NRs da unidade.", "resumo_indexado": True},
    ]})


class SessionMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    blocos: list[BlocoResposta] = Field(default_factory=list, max_length=10)


class SessionMessagesResponse(BaseModel):
    session_id: UUID
    status: Literal["ativa", "encerrando", "encerrada"]
    total: int = Field(ge=0)
    mensagens: list[SessionMessage]
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "session_id": "94229143-d20b-4766-80a4-05341435c236", "status": "encerrada", "total": 2,
        "mensagens": [{"role": "user", "content": "NRs para minha unidade"},
                      {"role": "assistant", "content": "Vamos conferir as atividades da unidade."}],
    }]})


SESSION_LIST_MAX_LIMIT = 100


class SessionListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(
        default=20, ge=1, le=SESSION_LIST_MAX_LIMIT,
        description="Quantidade de sessões por página (padrão 20, máximo 100).",
    )
    cursor: str | None = Field(
        default=None, min_length=1, max_length=1024,
        description="Cursor opaco retornado em next_cursor; válido somente para o mesmo usuário.",
    )


class SessionSummary(BaseModel):
    session_id: UUID = Field(description="UUID usado para consultar o histórico e retomar a conversa.")
    title: str = Field(max_length=80, description="Título derivado da primeira pergunta, em texto simples.")
    last_message_preview: str = Field(max_length=200, description="Prévia da última mensagem persistida, sem Markdown.")
    created_at: datetime = Field(description="Data de criação em ISO 8601 UTC.")
    updated_at: datetime = Field(description="Data da última atualização em ISO 8601 UTC.")
    status: Literal["ativa", "encerrando", "encerrada"]

    @field_validator("created_at", "updated_at")
    @classmethod
    def dates_in_utc(cls, value: datetime) -> datetime:
        # BSON legado sem tzinfo representa UTC, assim como o cliente Mongo atual.
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary]
    next_cursor: str | None = Field(description="Cursor da próxima página; null quando a listagem terminou.")
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "sessions": [{
            "session_id": "94229143-d20b-4766-80a4-05341435c236",
            "title": "NRs para minha unidade",
            "last_message_preview": "Vamos conferir as atividades da unidade…",
            "created_at": "2026-10-08T12:00:00Z",
            "updated_at": "2026-10-08T12:05:00Z",
            "status": "ativa",
        }],
        "next_cursor": None,
    }]})


class SessionErrorResponse(BaseModel):
    detail: str


class MemorySearch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    busca: str = Field(max_length=1000)


class InputDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisao: Literal["aprovar", "bloquear", "esclarecer"]
    motivo: Literal[
        "legitimo", "injecao_de_prompt", "acesso_nao_autorizado", "pedido_danoso",
        "fraude", "assedio", "contexto_insuficiente", "formato_nao_suportado",
    ]
    mensagem: str = Field(
        max_length=6000,
        description="Obrigatoriamente vazia quando decisao=aprovar; nos demais casos, mensagem curta de bloqueio ou esclarecimento. Nunca responder ao pedido original neste campo.",
    )

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
    esclarecer: str | None = Field(
        default=None, min_length=1, max_length=1000,
        description="Pergunta obrigatória e não vazia quando status=esclarecer ou aguardando_confirmacao; nunca null nesses status.",
    )
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
