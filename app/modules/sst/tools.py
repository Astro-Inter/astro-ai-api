import re
from math import ceil
from datetime import date, datetime
from typing import Annotated, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app.core import config
from app.modules.chat.schemas import SpecialistResult


COLLECTION_NRS = "nrs"
NR_TEXT_FIELDS = (
    "nome", "objetivo", "descricao", "aplicabilidade", "usabilidade",
)
NR_RETURN_FIELDS = (
    "nome", "objetivo", "descricao", "aplicabilidade", "revogada",
    "tempo_reciclagem_meses", "ultima_atualizacao", "data_criacao", "usabilidade",
)
NrNumber = Annotated[int, Field(ge=1, le=99)]
NrField = Literal[
    "objetivo", "descricao", "aplicabilidade", "revogada",
    "tempo_reciclagem_meses", "ultima_atualizacao", "data_criacao", "usabilidade",
]
NrQueryMode = Literal["automatico", "listar", "detalhar"]

_mongo_client = None


class ConsultarNrsArgs(BaseModel):
    """Filtros permitidos para consultar a collection de NRs."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    numeros: list[NrNumber] = Field(
        default_factory=list,
        max_length=10,
        description="Números das NRs desejadas, por exemplo [1] ou [6, 10, 35].",
    )
    termo: str | None = Field(
        default=None,
        min_length=2,
        max_length=200,
        description="Texto para procurar em nome, objetivo, descrição, aplicabilidade e usabilidade.",
    )
    revogada: bool | None = Field(
        default=None,
        description="Use true para revogadas, false para vigentes ou null para ambas.",
    )
    usabilidade: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        description="Público de uso da NR, quando informado pelo usuário.",
    )
    campos: list[NrField] = Field(
        default_factory=list,
        max_length=8,
        description="Campos específicos para detalhamento; vazio detalha todos apenas uma NR.",
    )
    modo: NrQueryMode = Field(
        default="automatico",
        description=(
            "Use listar para relações compactas, detalhar para conteúdo e automatico "
            "para inferir pelo número e pelos campos solicitados."
        ),
    )
    pagina: int = Field(
        default=1,
        ge=1,
        le=1000,
        description="Página da consulta, começando em 1.",
    )
    limite: int = Field(
        default=50,
        ge=1,
        le=50,
        description="Quantidade máxima por página, entre 1 e 50.",
    )

    @field_validator("numeros", "campos")
    @classmethod
    def sem_repeticoes(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Valores repetidos nao sao permitidos.")
        return values


class SstToolDecision(BaseModel):
    """Decisão do agente SST entre consultar NRs ou orientar sem consulta."""

    model_config = ConfigDict(extra="forbid")

    acao: Literal["consultar_nrs", "responder"]
    filtros: ConsultarNrsArgs | None = None
    resposta: SpecialistResult | None = None

    @model_validator(mode="before")
    @classmethod
    def normalizar_consulta_sem_filtros(cls, data):
        if isinstance(data, dict) and data.get("acao") == "consultar_nrs" \
                and data.get("filtros") is None:
            data = dict(data)
            data["filtros"] = {}
        return data

    @model_validator(mode="after")
    def validar_acao(self):
        if self.acao == "consultar_nrs" and (
            self.filtros is None or self.resposta is not None
        ):
            raise ValueError("A consulta de NRs exige filtros e nao aceita resposta.")
        if self.acao == "responder" and (self.resposta is None or self.filtros is not None):
            raise ValueError("A resposta direta exige resultado e nao aceita filtros.")
        if self.resposta is not None and self.resposta.dominio != "sst":
            raise ValueError("A resposta deve pertencer ao dominio de SST.")
        return self


def get_collection():
    """Obtém a collection fixa de NRs usando a conexão configurada do projeto."""
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(
            config.MONGODB_URI,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            timeoutMS=10000,
        )
    return _mongo_client[config.MONGODB_DATABASE][COLLECTION_NRS]


def _valor_publico(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


@tool("consultar_nrs", args_schema=ConsultarNrsArgs)
def consultar_nrs(
    numeros: list[int] | None = None,
    termo: str | None = None,
    revogada: bool | None = None,
    usabilidade: str | None = None,
    campos: list[NrField] | None = None,
    modo: NrQueryMode = "automatico",
    pagina: int = 1,
    limite: int = 50,
) -> dict:
    """Consulta uma ou várias Normas Regulamentadoras na collection `nrs`.

    Permite buscar pelo número da NR, por texto, vigência e público de uso. Use
    listagem paginada para relações de NRs e detalhamento para conteúdo específico.
    """
    if not config.MONGODB_URI or not config.MONGODB_DATABASE:
        return {"status": "indisponivel", "mensagem": "Consulta de NRs indisponivel."}

    query = {}
    if numeros:
        query["_id"] = {"$in": list(numeros)}
    if termo:
        safe_term = re.compile(re.escape(termo), re.IGNORECASE)
        query["$or"] = [{field: safe_term} for field in NR_TEXT_FIELDS]
    if revogada is not None:
        query["revogada"] = revogada
    if usabilidade:
        query["usabilidade"] = re.compile(
            f"^{re.escape(usabilidade)}$", re.IGNORECASE,
        )

    requested_fields = list(campos or [])
    effective_mode = modo
    if effective_mode == "automatico":
        effective_mode = "detalhar" if len(numeros or []) == 1 or requested_fields else "listar"

    if effective_mode == "listar":
        selected_fields = ["revogada", "ultima_atualizacao"]
    else:
        selected_fields = requested_fields or list(NR_RETURN_FIELDS)
        # Consultas detalhadas amplas permanecem limitadas para proteger o contexto.
        limite = min(limite, 10)

    projection = {"_id": 1, "nome": 1, **{field: 1 for field in selected_fields}}
    offset = (pagina - 1) * limite

    try:
        collection = get_collection()
        total = collection.count_documents(query)
        cursor = (
            collection.find(query, projection)
            .sort("_id", 1)
            .skip(offset)
            .limit(limite)
        )
        documents = list(cursor)
    except PyMongoError:
        return {"status": "indisponivel", "mensagem": "Consulta de NRs indisponivel."}

    nrs = []
    for document in documents:
        item = {"numero": document.get("_id")}
        for field in ("nome", *selected_fields):
            if field in document:
                item[field] = _valor_publico(document[field])
        if effective_mode == "listar":
            item["situacao"] = "REVOGADA" if document.get("revogada") else "VIGENTE"
            item.pop("revogada", None)
        nrs.append(item)

    total_pages = ceil(total / limite) if total else 0

    return {
        "status": "ok" if nrs else "sem_dados",
        "quantidade": len(nrs),
        "modo": effective_mode,
        "nrs": nrs,
        "fonte": {"tipo": "mongodb", "collection": COLLECTION_NRS},
        "paginacao": {
            "pagina": pagina,
            "limite": limite,
            "total": total,
            "total_paginas": total_pages,
            "tem_proxima_pagina": pagina < total_pages,
        },
    }


TOOLS_SST = [consultar_nrs]
