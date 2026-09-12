import re
from datetime import date, datetime
from math import ceil
from typing import Annotated, Literal

import psycopg
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app.core import config as app_config
from app.core.security import CurrentUser
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

    acao: Literal["consultar_nrs", "consultar_nrs_obrigatorias", "responder"]
    filtros: ConsultarNrsArgs | None = None
    resposta: SpecialistResult | None = None

    @model_validator(mode="before")
    @classmethod
    def normalizar_consulta_sem_filtros(cls, data):
        if isinstance(data, dict) and data.get("acao") == "consultar_nrs" \
                and data.get("filtros") is None:
            data = dict(data)
            data["filtros"] = {}
        if isinstance(data, dict) and data.get("acao") == "consultar_nrs_obrigatorias":
            data = dict(data)
            data["filtros"] = None
        return data

    @model_validator(mode="after")
    def validar_acao(self):
        if self.acao == "consultar_nrs" and (
            self.filtros is None or self.resposta is not None
        ):
            raise ValueError("A consulta de NRs exige filtros e nao aceita resposta.")
        if self.acao == "consultar_nrs_obrigatorias" and (
            self.filtros is not None or self.resposta is not None
        ):
            raise ValueError("A consulta de NRs obrigatorias nao aceita filtros nem resposta.")
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
            app_config.MONGODB_URI,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            timeoutMS=10000,
        )
    return _mongo_client[app_config.MONGODB_DATABASE][COLLECTION_NRS]


def get_postgres_connection():
    """Abre uma conexão curta e somente leitura com o PostgreSQL."""
    return psycopg.connect(
        app_config.DATABASE_URL,
        autocommit=True,
        connect_timeout=5,
        options="-c statement_timeout=5000 -c default_transaction_read_only=on",
    )


def _usuario_do_contexto(runtime_config: RunnableConfig) -> CurrentUser | None:
    configurable = (runtime_config or {}).get("configurable", {})
    raw_user = configurable.get("usuario_atual")
    try:
        return raw_user if isinstance(raw_user, CurrentUser) else CurrentUser.model_validate(raw_user)
    except Exception:
        return None


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
    if not app_config.MONGODB_URI or not app_config.MONGODB_DATABASE:
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


@tool("consultar_nrs_obrigatorias")
def consultar_nrs_obrigatorias(config: RunnableConfig = None) -> dict:
    """Consulta as NRs vigentes obrigatórias para o cargo do usuário autenticado.

    A identidade vem exclusivamente do contexto seguro da requisição. A ferramenta
    não recebe UID, cargo ou outro seletor controlado pelo modelo ou pelo usuário.
    """
    user = _usuario_do_contexto(config)
    if user is None:
        return {"status": "erro", "mensagem": "Usuario nao identificado no contexto."}
    if user.role == "ADMIN":
        return {
            "status": "nao_aplicavel",
            "mensagem": "Administradores nao possuem cargo funcional associado.",
        }
    if not app_config.DATABASE_URL:
        return {
            "status": "indisponivel",
            "mensagem": "Consulta de NRs obrigatorias indisponivel.",
        }

    query = """
        SELECT usuario.nome AS usuario,
               cargo.nome AS cargo,
               unidade.nome AS unidade,
               nr_catalogo.codigo_nr AS numero,
               nr_catalogo.titulo AS titulo,
               nr_catalogo.tempo_reciclagem_mes AS tempo_reciclagem_meses
          FROM usuario
          JOIN cargo
            ON cargo.id_cargo = usuario.cargo_id
          JOIN unidade
            ON unidade.id_unidade = usuario.unidade_id
          LEFT JOIN cargo_nr
            ON cargo_nr.cargo_id = cargo.id_cargo
          LEFT JOIN unidade_nr
            ON unidade_nr.unidade_id = unidade.id_unidade
           AND unidade_nr.nr_id = cargo_nr.nr_id
          LEFT JOIN nr_catalogo
            ON nr_catalogo.codigo_nr = unidade_nr.nr_id
           AND nr_catalogo.revogada = FALSE
         WHERE usuario.firebase_uid = %s
         ORDER BY nr_catalogo.codigo_nr
    """
    try:
        with get_postgres_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, [user.uid])
                rows = cursor.fetchall()
    except Exception:
        return {
            "status": "indisponivel",
            "mensagem": "Consulta de NRs obrigatorias indisponivel.",
        }

    if not rows:
        return {
            "status": "sem_dados",
            "mensagem": "Usuario autenticado nao encontrado no cadastro funcional.",
        }

    employee, role_name, unit = rows[0][:3]
    nrs = [{
        "numero": row[3],
        "titulo": row[4],
        "tempo_reciclagem_meses": row[5],
    } for row in rows if row[3] is not None]
    return {
        "status": "ok",
        "usuario": employee,
        "cargo": role_name,
        "unidade": unit,
        "quantidade": len(nrs),
        "nrs": nrs,
        "fonte": {
            "tipo": "postgresql",
            "tabelas": [
                "usuario", "cargo", "unidade", "cargo_nr", "unidade_nr", "nr_catalogo",
            ],
        },
    }


TOOLS_SST = [consultar_nrs, consultar_nrs_obrigatorias]
