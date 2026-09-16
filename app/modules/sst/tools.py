import asyncio
import re
import unicodedata
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
from app.infrastructure.mcp_fetch import get_fetch_mcp_client
from app.modules.chat.schemas import SpecialistResult
from app.modules.shared.public_sources import PublicSourceId, consultar_fontes_publicas


COLLECTION_NRS = "nrs"
NR_OFFICIAL_CATALOG_URL = (
    "https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/"
    "seguranca-e-saude-no-trabalho/ctpp-nrs/normas-regulamentadoras-nrs"
)
NR_OFFICIAL_DETAIL_BASE_URL = (
    "https://www.gov.br/trabalho-e-emprego/pt-br/acesso-a-informacao/"
    "participacao-social/conselhos-e-orgaos-colegiados/"
    "comissao-tripartite-partitaria-permanente/normas-regulamentadora/"
    "normas-regulamentadoras-vigentes"
)
NR_DETAIL_URL_OVERRIDES = {1: f"{NR_OFFICIAL_DETAIL_BASE_URL}/nr-1"}
REVOKED_NRS = frozenset({2, 27})
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


class ConsultarOrientacoesSstArgs(BaseModel):
    """Pesquisa permitida nos catálogos públicos oficiais de SST."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    termo: str = Field(
        min_length=2,
        max_length=200,
        description=(
            "Assunto a localizar em cartilhas, manuais, guias ou orientações "
            "oficiais, por exemplo 'riscos psicossociais' ou 'higiene das mãos'."
        ),
    )
    fontes: list[PublicSourceId] = Field(
        default_factory=list,
        max_length=4,
        description="Fontes oficiais desejadas; vazio consulta MTE, Fundacentro e Anvisa.",
    )

    @field_validator("fontes")
    @classmethod
    def fontes_sem_repeticoes(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Fontes repetidas nao sao permitidas.")
        return values


class ConsultarNrsOrganizacaoArgs(BaseModel):
    """O escopo é público; empresa e unidade vêm apenas da autenticação."""

    model_config = ConfigDict(extra="forbid")
    escopo: Literal["unidade", "empresa"] = "unidade"


class ConsultarConformidadeUsuarioArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    pessoa: str = Field(min_length=1, max_length=255, description="Nome ou e-mail da pessoa; nunca UID ou ID interno.")


class SstToolDecision(BaseModel):
    """Decisão do agente SST entre consultar NRs ou orientar sem consulta."""

    model_config = ConfigDict(extra="forbid")

    acao: Literal[
        "consultar_nrs_organizacao",
        "consultar_nrs",
        "consultar_nrs_obrigatorias",
        "consultar_situacao_nrs",
        "consultar_conformidade_usuario",
        "consultar_orientacoes_sst",
        "responder",
    ]
    filtros: ConsultarConformidadeUsuarioArgs | ConsultarNrsOrganizacaoArgs | ConsultarOrientacoesSstArgs | ConsultarNrsArgs | None = None
    resposta: SpecialistResult | None = None

    @model_validator(mode="before")
    @classmethod
    def normalizar_consulta_sem_filtros(cls, data):
        if isinstance(data, dict) and data.get("acao") == "consultar_conformidade_usuario":
            data = {**data, "filtros": ConsultarConformidadeUsuarioArgs.model_validate(data.get("filtros") or {})}
        if isinstance(data, dict) and data.get("acao") == "consultar_nrs_organizacao":
            data = {**data, "filtros": ConsultarNrsOrganizacaoArgs.model_validate(data.get("filtros") or {})}
        if isinstance(data, dict) and data.get("acao") == "consultar_nrs" \
                and data.get("filtros") is None:
            data = dict(data)
            data["filtros"] = ConsultarNrsArgs()
        if isinstance(data, dict) and data.get("acao") == "consultar_nrs" \
                and not isinstance(data.get("filtros"), ConsultarNrsArgs):
            data = {**data, "filtros": ConsultarNrsArgs.model_validate(data["filtros"])}
        if isinstance(data, dict) and data.get("acao") in {
            "consultar_nrs_obrigatorias", "consultar_situacao_nrs",
        }:
            data = dict(data)
            data["filtros"] = None
        return data

    @model_validator(mode="after")
    def validar_acao(self):
        if self.acao == "consultar_conformidade_usuario" and (
            not isinstance(self.filtros, ConsultarConformidadeUsuarioArgs) or self.resposta is not None
        ):
            raise ValueError("Consulta de conformidade exige pessoa e nao aceita resposta.")
        if self.acao == "consultar_nrs_organizacao" and (
            not isinstance(self.filtros, ConsultarNrsOrganizacaoArgs) or self.resposta is not None
        ):
            raise ValueError("Consulta organizacional exige filtros e nao aceita resposta.")
        if self.acao == "consultar_nrs" and (
            not isinstance(self.filtros, ConsultarNrsArgs) or self.resposta is not None
        ):
            raise ValueError("A consulta de NRs exige filtros e nao aceita resposta.")
        if self.acao == "consultar_orientacoes_sst" and (
            not isinstance(self.filtros, ConsultarOrientacoesSstArgs)
            or self.resposta is not None
        ):
            raise ValueError("A consulta de orientacoes de SST exige filtros e nao aceita resposta.")
        if self.acao == "consultar_nrs_obrigatorias" and (
            self.filtros is not None or self.resposta is not None
        ):
            raise ValueError("A consulta de NRs obrigatorias nao aceita filtros nem resposta.")
        if self.acao == "consultar_situacao_nrs" and (
            self.filtros is not None or self.resposta is not None
        ):
            raise ValueError("A consulta da situacao das NRs nao aceita filtros nem resposta.")
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


def _texto_pesquisavel(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(character for character in normalized if not unicodedata.combining(character)).casefold()


def _pagina_oficial_nr(numero: int) -> str:
    return NR_DETAIL_URL_OVERRIDES.get(
        numero,
        f"{NR_OFFICIAL_DETAIL_BASE_URL}/norma-regulamentadora-no-{numero}-nr-{numero}",
    )


def _executar_fetch(url: str, *, max_length: int) -> dict:
    try:
        return asyncio.run(
            get_fetch_mcp_client().fetch(url, max_length=max_length),
        )
    except Exception:
        return {"status": "indisponivel"}


def _parsear_catalogo_oficial(content: str) -> tuple[list[dict], str | None]:
    updated = re.search(r"Atualizado em\s+(\d{2}/\d{2}/\d{4})", content, re.IGNORECASE)
    page_updated = updated.group(1) if updated else None
    entries = []
    seen = set()
    pattern = re.compile(
        r"^\s*NR\s*-\s*0?(\d{1,2})\s*[-–—]\s*(.+?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    for match in pattern.finditer(content):
        number = int(match.group(1))
        if number in seen:
            continue
        raw_name = re.sub(r"\s+", " ", match.group(2)).strip(" *_")
        revoked = bool(re.search(r"\(\s*REVOGADA\s*\)\s*$", raw_name, re.IGNORECASE))
        name = re.sub(r"\s*\(\s*REVOGADA\s*\)\s*$", "", raw_name, flags=re.IGNORECASE)
        entries.append({
            "numero": number,
            "nome": name,
            "situacao": "REVOGADA" if revoked else "VIGENTE",
            "fonte_oficial": NR_OFFICIAL_CATALOG_URL,
        })
        seen.add(number)
    return entries, page_updated


def _resumir_pagina_oficial(content: str) -> tuple[str | None, str | None]:
    updated = re.search(r"Atualizado em\s+(\d{2}/\d{2}/\d{4})", content, re.IGNORECASE)
    page_updated = updated.group(1) if updated else None
    body = content[updated.end():] if updated else content
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", body):
        clean = re.sub(r"<[^>]+>", " ", paragraph)
        clean = re.sub(r"\s+", " ", clean).strip(" #*-")
        if len(clean) < 100:
            continue
        lowered = _texto_pesquisavel(clean)
        if any(marker in lowered for marker in (
            "compartilhe por facebook", "termos mais buscados", "acesso a informacao",
        )):
            continue
        paragraphs.append(clean)
        if len(paragraphs) == 2:
            break
    summary = "\n\n".join(paragraphs)
    return (summary[:1600].rstrip() or None), page_updated


def _consultar_fonte_oficial(numeros: list[int], modo: str) -> dict:
    if modo == "detalhar" and len(numeros) == 1 and numeros[0] not in REVOKED_NRS:
        number = numeros[0]
        url = _pagina_oficial_nr(number)
        fetched = _executar_fetch(url, max_length=9000)
        if fetched.get("status") == "ok":
            summary, page_updated = _resumir_pagina_oficial(fetched["conteudo"])
            if summary:
                return {
                    "status": "ok",
                    "tipo": "detalhe",
                    "url": url,
                    "numero": number,
                    "resumo": summary,
                    "pagina_atualizada_em": page_updated,
                }

    fetched = _executar_fetch(NR_OFFICIAL_CATALOG_URL, max_length=16000)
    if fetched.get("status") != "ok":
        return {"status": "indisponivel"}
    entries, page_updated = _parsear_catalogo_oficial(fetched["conteudo"])
    if not entries:
        return {"status": "indisponivel"}
    return {
        "status": "ok",
        "tipo": "catalogo",
        "url": NR_OFFICIAL_CATALOG_URL,
        "nrs": entries,
        "pagina_atualizada_em": page_updated,
    }


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
    """Consulta NRs no portal oficial e complementa com o contexto do Astro.

    A fonte primária é o Ministério do Trabalho e Emprego, acessado pelo servidor
    MCP Fetch. A collection `nrs` é secundária e registra como as normas estão
    representadas no projeto.
    """

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

    mongo_available = bool(app_config.MONGODB_URI and app_config.MONGODB_DATABASE)
    mongo_total = 0
    documents = []
    if mongo_available:
        try:
            collection = get_collection()
            mongo_total = collection.count_documents(query)
            cursor = (
                collection.find(query, projection)
                .sort("_id", 1)
                .skip(offset)
                .limit(limite)
            )
            documents = list(cursor)
        except PyMongoError:
            mongo_available = False

    requested_numbers = list(numeros or [])
    official = _consultar_fonte_oficial(requested_numbers, effective_mode)
    document_by_number = {document.get("_id"): document for document in documents}
    nrs = []
    total = mongo_total
    primary_source = None

    if official.get("status") == "ok" and official.get("tipo") == "detalhe":
        number = official["numero"]
        document = document_by_number.get(number, {})
        if not usabilidade or document:
            item = {
                "numero": number,
                "nome": document.get("nome", f"Norma Regulamentadora nº {number}"),
                "situacao": "VIGENTE",
                "resumo_oficial": official["resumo"],
                "pagina_oficial_atualizada_em": official.get("pagina_atualizada_em"),
                "fonte_oficial": official["url"],
            }
            for field in selected_fields:
                if field in document:
                    item[field] = _valor_publico(document[field])
            nrs = [item]
            total = 1
        primary_source = {
            "tipo": "web",
            "titulo": f"Ministério do Trabalho e Emprego — NR-{number}",
            "url": official["url"],
            "protocolo": "MCP Fetch",
        }
    elif official.get("status") == "ok":
        official_entries = official["nrs"]
        internal_numbers = set(document_by_number)
        requested_set = set(requested_numbers)
        normalized_term = _texto_pesquisavel(termo) if termo else None
        filtered = []
        for entry in official_entries:
            number = entry["numero"]
            if requested_set and number not in requested_set:
                continue
            if revogada is not None and (entry["situacao"] == "REVOGADA") != revogada:
                continue
            if usabilidade and number not in internal_numbers:
                continue
            if normalized_term and (
                normalized_term not in _texto_pesquisavel(entry["nome"])
                and number not in internal_numbers
            ):
                continue
            filtered.append(entry)
        total = len(filtered)
        selected_official = filtered[offset:offset + limite]
        for entry in selected_official:
            item = dict(entry)
            document = document_by_number.get(entry["numero"], {})
            for field in selected_fields:
                if field == "nome" and item.get("nome"):
                    continue
                if field in document:
                    item[field] = _valor_publico(document[field])
            if effective_mode == "detalhar":
                item["fonte_oficial"] = official["url"]
            nrs.append(item)
        primary_source = {
            "tipo": "web",
            "titulo": "Normas Regulamentadoras — Ministério do Trabalho e Emprego",
            "url": official["url"],
            "protocolo": "MCP Fetch",
            "pagina_atualizada_em": official.get("pagina_atualizada_em"),
        }
    else:
        for document in documents:
            item = {"numero": document.get("_id")}
            for field in ("nome", *selected_fields):
                if field in document:
                    item[field] = _valor_publico(document[field])
            if effective_mode == "listar":
                item["situacao"] = "REVOGADA" if document.get("revogada") else "VIGENTE"
                item.pop("revogada", None)
            nrs.append(item)
        primary_source = (
            {"tipo": "mongodb", "collection": COLLECTION_NRS}
            if mongo_available else None
        )

    if not nrs and not mongo_available and official.get("status") != "ok":
        return {"status": "indisponivel", "mensagem": "Consulta de NRs indisponivel."}

    total_pages = ceil(total / limite) if total else 0
    sources = []
    if primary_source:
        sources.append(primary_source)
    if mongo_available:
        sources.append({
            "tipo": "mongodb",
            "titulo": "Contexto interno de NRs do Astro",
            "collection": COLLECTION_NRS,
        })
    return {
        "status": "ok" if nrs else "sem_dados",
        "quantidade": len(nrs),
        "modo": effective_mode,
        "origem_principal": "web_oficial" if official.get("status") == "ok" else "base_interna",
        "nrs": nrs,
        "fonte": primary_source,
        "fontes": sources,
        "paginacao": {
            "pagina": pagina,
            "limite": limite,
            "total": total,
            "total_paginas": total_pages,
            "tem_proxima_pagina": pagina < total_pages,
        },
    }


@tool("consultar_orientacoes_sst", args_schema=ConsultarOrientacoesSstArgs)
async def consultar_orientacoes_sst(
    termo: str,
    fontes: list[PublicSourceId] | None = None,
) -> dict:
    """Busca cartilhas, manuais e orientações oficiais sobre SST.

    A consulta usa somente páginas previamente catalogadas do MTE, da
    Fundacentro e da Anvisa. URLs livres não são aceitas pelo modelo.
    """
    return await consultar_fontes_publicas(
        termo,
        area="sst_geral",
        fontes=fontes,
    )


@tool("consultar_nrs_organizacao", args_schema=ConsultarNrsOrganizacaoArgs)
def consultar_nrs_organizacao(
    escopo: Literal["unidade", "empresa"] = "unidade",
    config: RunnableConfig = None,
) -> dict:
    """Lista NRs vinculadas à unidade atual ou à união das unidades da empresa.

    Empresa significa o workspace da unidade do usuário autenticado. A consulta
    não aplica cargo, não deduz conformidade e não acessa outras empresas.
    Inclui vínculos de todas as unidades, inclusive inativas, e informa revogação.
    """
    user = _usuario_do_contexto(config)
    if user is None:
        return {"status": "erro", "mensagem": "Usuário não identificado no contexto."}
    if not app_config.DATABASE_URL:
        return {"status": "indisponivel", "mensagem": "Consulta das NRs da organização indisponível."}
    query = """
        WITH contexto AS (
            SELECT atual.id_unidade, atual.workspace_id,
                   atual.nome AS unidade, workspace.nome AS empresa
              FROM usuario
              JOIN unidade AS atual ON atual.id_unidade = usuario.unidade_id
              JOIN workspace ON workspace.id_workspace = atual.workspace_id
             WHERE usuario.firebase_uid = %s
        )
        SELECT DISTINCT contexto.empresa, contexto.unidade,
               nr.codigo_nr, nr.titulo, nr.revogada
          FROM contexto
          LEFT JOIN unidade AS alvo ON (
              (%s = 'empresa' AND alvo.workspace_id = contexto.workspace_id)
              OR (%s = 'unidade' AND alvo.id_unidade = contexto.id_unidade)
          )
          LEFT JOIN unidade_nr ON unidade_nr.unidade_id = alvo.id_unidade
          LEFT JOIN nr_catalogo AS nr ON nr.codigo_nr = unidade_nr.nr_id
         ORDER BY nr.codigo_nr NULLS LAST
    """
    try:
        with get_postgres_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, [user.uid, escopo, escopo])
                rows = cursor.fetchall()
    except Exception:
        return {"status": "indisponivel", "mensagem": "Consulta das NRs da organização indisponível."}
    if not rows:
        return {"status": "nao_aplicavel", "mensagem": "Não encontrei unidade e empresa vinculadas ao seu cadastro."}
    nrs = [{"numero": row[2], "titulo": row[3], "revogada": row[4]}
           for row in rows if row[2] is not None]
    return {
        "status": "ok", "escopo": escopo, "empresa": rows[0][0],
        "unidade": rows[0][1], "quantidade": len(nrs), "nrs": nrs,
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


@tool("consultar_situacao_nrs")
def consultar_situacao_nrs(config: RunnableConfig = None) -> dict:
    """Consulta a situação das NRs obrigatórias do usuário autenticado.

    Identifica NRs vigentes, pendentes, vencidas ou ainda não realizadas e
    informa a ação necessária. A identidade vem exclusivamente do contexto
    autenticado; a ferramenta não aceita UID ou filtros definidos pelo modelo.
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
            "mensagem": "Consulta da situacao das NRs indisponivel.",
        }

    return _consultar_situacao_nrs_filtrada("usuario.firebase_uid = %s", [user.uid])


def _consultar_situacao_nrs_filtrada(condition: str, parameters: list) -> dict:
    """Compartilha os critérios de validade com predicados internos autorizados."""
    query = """
        WITH usuario_atual AS (
            SELECT usuario.id_usuario,
                   usuario.nome AS usuario,
                   cargo.id_cargo,
                   cargo.nome AS cargo,
                   unidade.id_unidade,
                   unidade.nome AS unidade
              FROM usuario
              JOIN cargo
                ON cargo.id_cargo = usuario.cargo_id
              JOIN unidade
                ON unidade.id_unidade = usuario.unidade_id
             WHERE usuario.firebase_uid = %s
             LIMIT 1
        ),
        nrs_obrigatorias AS (
            SELECT usuario_atual.*,
                   nr_catalogo.codigo_nr AS numero,
                   nr_catalogo.titulo
              FROM usuario_atual
              LEFT JOIN cargo_nr
                ON cargo_nr.cargo_id = usuario_atual.id_cargo
              LEFT JOIN unidade_nr
                ON unidade_nr.unidade_id = usuario_atual.id_unidade
               AND unidade_nr.nr_id = cargo_nr.nr_id
              LEFT JOIN nr_catalogo
                ON nr_catalogo.codigo_nr = unidade_nr.nr_id
               AND nr_catalogo.revogada = FALSE
        )
        SELECT nrs_obrigatorias.usuario,
               nrs_obrigatorias.cargo,
               nrs_obrigatorias.unidade,
               nrs_obrigatorias.numero,
               nrs_obrigatorias.titulo,
               conformidade_atual.data_validade,
               pendencia.data_inicial,
               pendencia.data_termino,
               CASE
                   WHEN nrs_obrigatorias.numero IS NULL THEN NULL
                   WHEN conformidade_atual.data_validade >= CURRENT_DATE THEN 'VIGENTE'
                   WHEN pendencia.id_turma_funcionario IS NOT NULL THEN 'PENDENTE'
                   WHEN conformidade_atual.data_validade < CURRENT_DATE
                       THEN 'RENOVACAO_NECESSARIA'
                   ELSE 'REALIZACAO_NECESSARIA'
               END AS situacao,
               CASE
                   WHEN nrs_obrigatorias.numero IS NULL THEN NULL
                   WHEN pendencia.id_turma_funcionario IS NOT NULL
                       THEN 'CONCLUIR_PENDENCIA'
                   WHEN conformidade_atual.data_validade > CURRENT_DATE + 30 THEN 'NENHUMA'
                   WHEN conformidade_atual.data_validade >= CURRENT_DATE
                       THEN 'RENOVAR_EM_BREVE'
                   WHEN conformidade_atual.data_validade < CURRENT_DATE THEN 'RENOVAR'
                   ELSE 'REALIZAR'
               END AS acao_necessaria,
               CURRENT_DATE AS data_referencia
          FROM nrs_obrigatorias
          LEFT JOIN LATERAL (
              SELECT conformidade.data_validade
                FROM conformidade
               WHERE conformidade.usuario_id = nrs_obrigatorias.id_usuario
                 AND conformidade.nr_id = nrs_obrigatorias.numero
                 AND conformidade.aplicavel IS TRUE
               ORDER BY conformidade.data_validade DESC NULLS LAST,
                        conformidade.id_conformidade DESC
               LIMIT 1
          ) AS conformidade_atual ON TRUE
          LEFT JOIN LATERAL (
              SELECT turma_funcionario.id_turma_funcionario,
                     turma.data_inicial,
                     turma.data_termino
                FROM turma_funcionario
                JOIN turma
                  ON turma.id_turma = turma_funcionario.turma_id
                JOIN evento
                  ON evento.id_evento = turma.evento_id
                LEFT JOIN conclusao_evento
                  ON conclusao_evento.turma_funcionario_id =
                     turma_funcionario.id_turma_funcionario
               WHERE turma_funcionario.usuario_id = nrs_obrigatorias.id_usuario
                 AND evento.nr_id = nrs_obrigatorias.numero
                 AND evento.status <> 'CANCELADO'
                 AND COALESCE(conclusao_evento.status, 'PENDENTE') = 'PENDENTE'
               ORDER BY turma.data_inicial
               LIMIT 1
          ) AS pendencia ON TRUE
         ORDER BY nrs_obrigatorias.numero
    """
    query = query.replace("usuario.firebase_uid = %s", condition)
    try:
        with get_postgres_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, parameters)
                rows = cursor.fetchall()
    except Exception:
        return {
            "status": "indisponivel",
            "mensagem": "Consulta da situacao das NRs indisponivel.",
        }

    if not rows:
        return {
            "status": "sem_dados",
            "mensagem": "Nao encontrei cadastro funcional no escopo permitido.",
        }

    employee, role_name, unit = rows[0][:3]
    nrs = []
    for row in rows:
        if row[3] is None:
            continue
        nrs.append({
            "numero": row[3],
            "titulo": row[4],
            "situacao": row[8],
            "acao_necessaria": row[9],
            "data_validade": _valor_publico(row[5]),
            "atividade_pendente": row[6] is not None,
            "data_inicio_pendencia": _valor_publico(row[6]),
            "data_termino_pendencia": _valor_publico(row[7]),
        })
    return {
        "status": "ok",
        "usuario": employee,
        "cargo": role_name,
        "unidade": unit,
        "data_referencia": _valor_publico(rows[0][10]),
        "quantidade": len(nrs),
        "nrs": nrs,
        "fonte": {
            "tipo": "postgresql",
            "tabelas": [
                "usuario", "cargo", "unidade", "cargo_nr", "unidade_nr",
                "nr_catalogo", "conformidade", "turma_funcionario", "turma",
                "evento", "conclusao_evento",
            ],
        },
    }


def _escopo_conformidade(user: CurrentUser) -> tuple[str, list]:
    if user.role == "GESTOR_WORKSPACE":
        scope = "unidade.workspace_id = (SELECT u.workspace_id FROM usuario a JOIN unidade u ON u.id_unidade = a.unidade_id WHERE a.firebase_uid = %s LIMIT 1)"
        types = ["GESTOR", "GESTOR_WORKSPACE", "FUNCIONARIO"]
    else:
        scope = "usuario.unidade_id = (SELECT a.unidade_id FROM usuario a WHERE a.firebase_uid = %s LIMIT 1)"
        types = ["GESTOR", "FUNCIONARIO"]
    return scope + " AND usuario.tipo = ANY(%s) AND usuario.firebase_uid <> %s", [user.uid, types, user.uid]


@tool("consultar_conformidade_usuario", args_schema=ConsultarConformidadeUsuarioArgs)
def consultar_conformidade_usuario(pessoa: str, config: RunnableConfig = None) -> dict:
    """Consulta NRs obrigatórias e validade de outra pessoa no escopo do gestor.

    Gestor vê sua unidade; gestor de workspace vê apenas seu workspace.
    Não avalia aptidão médica nem certifica conformidade legal da empresa.
    """
    user = _usuario_do_contexto(config)
    if user is None or user.role not in {"GESTOR", "GESTOR_WORKSPACE"}:
        return {"status": "nao_autorizado", "mensagem": "Somente gestores podem consultar a conformidade de outras pessoas no seu escopo."}
    person = pessoa.strip()
    if not person or person.casefold() in {"ela", "ele", "dela", "dele", "essa pessoa", "esta pessoa"}:
        return {"status": "esclarecer", "mensagem": "Informe o nome ou e-mail da pessoa cuja conformidade deseja consultar."}
    if not app_config.DATABASE_URL:
        return {"status": "indisponivel", "mensagem": "Consulta de conformidade indisponivel."}
    scope, scope_parameters = _escopo_conformidade(user)
    selector = "LOWER(usuario.email) = LOWER(%s)" if "@" in person else "LOWER(usuario.nome) = LOWER(%s)"
    query = "SELECT usuario.firebase_uid, usuario.nome, usuario.email FROM usuario JOIN unidade ON unidade.id_unidade = usuario.unidade_id WHERE " + selector + " AND " + scope + " ORDER BY usuario.nome, usuario.email LIMIT 2"
    try:
        with get_postgres_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, [person, *scope_parameters])
                people = cursor.fetchall()
    except Exception:
        return {"status": "indisponivel", "mensagem": "Consulta de conformidade indisponivel."}
    if not people:
        return {"status": "sem_dados", "mensagem": "Nao encontrei essa pessoa no seu escopo de acesso. Informe o nome completo ou e-mail."}
    if len(people) > 1:
        return {"status": "esclarecer", "mensagem": "Há mais de uma pessoa com esse nome. Informe o e-mail para consultar a pessoa correta."}
    # Revalida o escopo na leitura de conformidade, inclusive após a resolução.
    result = _consultar_situacao_nrs_filtrada("usuario.firebase_uid = %s AND " + scope, [people[0][0], *scope_parameters])
    return {**result, "consulta_terceiro": True}


TOOLS_SST = [
    consultar_conformidade_usuario,
    consultar_nrs_organizacao,
    consultar_nrs,
    consultar_orientacoes_sst,
    consultar_nrs_obrigatorias,
    consultar_situacao_nrs,
]
