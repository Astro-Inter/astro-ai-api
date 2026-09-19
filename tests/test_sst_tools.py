from datetime import date, datetime, timezone
import re

import pytest
from pydantic import ValidationError
from pymongo.errors import ServerSelectionTimeoutError

from app.core import config
from app.modules.sst import tools as sst_tools
from app.modules.sst.tools import (
    ConsultarNrsArgs,
    ConsultarOrientacoesSstArgs,
    SstToolDecision,
    consultar_nrs,
    consultar_orientacoes_sst,
)
from app.modules.sst.tools import consultar_nrs_obrigatorias, consultar_situacao_nrs
from app.modules.sst.tools import ConsultarNrsOrganizacaoArgs, consultar_nrs_organizacao


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents
        self.sort_args = None
        self.skip_value = 0
        self.limit_value = None

    def sort(self, field, direction):
        self.sort_args = (field, direction)
        return self

    def skip(self, value):
        self.skip_value = value
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def __iter__(self):
        end = self.skip_value + self.limit_value
        return iter(self.documents[self.skip_value:end])


class FakeCollection:
    def __init__(self, documents=None, error=None):
        self.documents = documents or []
        self.error = error
        self.query = None
        self.projection = None
        self.cursor = None

    def find(self, query, projection):
        if self.error:
            raise self.error
        self.query = query
        self.projection = projection
        self.cursor = FakeCursor(self.documents)
        return self.cursor

    def count_documents(self, query):
        if self.error:
            raise self.error
        self.count_query = query
        return len(self.documents)


class FakePostgresCursor:
    def __init__(self, rows):
        self.rows = rows
        self.query = None
        self.parameters = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query, parameters):
        self.query = query
        self.parameters = parameters

    def fetchall(self):
        return self.rows


class FakePostgresConnection:
    def __init__(self, rows):
        self.db_cursor = FakePostgresCursor(rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def cursor(self):
        return self.db_cursor


class UnavailableFetchClient:
    async def fetch(self, *_args, **_kwargs):
        return {"status": "indisponivel"}


@pytest.mark.parametrize("role,scope", [("GESTOR", "usuario.unidade_id ="), ("GESTOR_WORKSPACE", "unidade.workspace_id =")])
def test_consultar_conformidade_revalidates_manager_scope(monkeypatch, role, scope):
    resolver = FakePostgresConnection([("target-uid", "Maria Silva", "maria@example.com")])
    details = FakePostgresConnection([
        ("Maria Silva", "Eletricista", "Matriz", 10, "Eletricidade", date(2027, 1, 1), None, None, "VIGENTE", "NENHUMA", date(2026, 9, 15)),
        ("Maria Silva", "Eletricista", "Matriz", 35, "Altura", None, None, None, "REALIZACAO_NECESSARIA", "REALIZAR", date(2026, 9, 15)),
    ])
    connections = iter([resolver, details])
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(sst_tools, "get_postgres_connection", lambda: next(connections))
    result = sst_tools.consultar_conformidade_usuario.invoke({"pessoa": "maria@example.com"}, config={"configurable": {"usuario_atual": {"uid": "manager", "role": role}}})
    assert result["status"] == "ok" and result["usuario"] == "Maria Silva"
    assert result["nrs"][1]["acao_necessaria"] == "REALIZAR"
    assert "target-uid" not in str(result)
    for connection in [resolver, details]:
        assert scope in connection.db_cursor.query
        assert "usuario.tipo = ANY(%s)" in connection.db_cursor.query
        assert "manager" in connection.db_cursor.parameters
    assert resolver.db_cursor.parameters[0] == "maria@example.com"
    assert details.db_cursor.parameters[0] == "target-uid"


@pytest.mark.parametrize("role", ["COLABORADOR", "ADMIN"])
def test_consultar_conformidade_denies_non_managers_without_database(monkeypatch, role):
    monkeypatch.setattr(sst_tools, "get_postgres_connection", lambda: pytest.fail("Não deveria consultar o banco"))
    result = sst_tools.consultar_conformidade_usuario.invoke({"pessoa": "Maria"}, config={"configurable": {"usuario_atual": {"uid": "owner", "role": role}}})
    assert result["status"] == "nao_autorizado"


@pytest.mark.parametrize("rows,status", [([], "sem_dados"), ([("a", "Maria", "a@example.com"), ("b", "Maria", "b@example.com")], "esclarecer")])
def test_consultar_conformidade_missing_or_ambiguous_person(monkeypatch, rows, status):
    connection = FakePostgresConnection(rows)
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(sst_tools, "get_postgres_connection", lambda: connection)
    result = sst_tools.consultar_conformidade_usuario.invoke({"pessoa": "Maria"}, config={"configurable": {"usuario_atual": {"uid": "manager", "role": "GESTOR"}}})
    assert result["status"] == status and "nrs" not in result
    assert connection.db_cursor.parameters[0] == "Maria"
    assert "LIMIT 2" in connection.db_cursor.query


def test_conformidade_schema_rejects_ids_and_wrong_filter_type():
    for filters in [{"pessoa": "Maria", "uid": "other"}, {"numeros": [1]}, {}]:
        with pytest.raises(ValidationError):
            SstToolDecision.model_validate({"acao": "consultar_conformidade_usuario", "filtros": filters})
    decision = SstToolDecision.model_validate({"acao": "consultar_conformidade_usuario", "filtros": {"pessoa": "Maria Silva"}})
    assert decision.filtros.pessoa == "Maria Silva"


def test_conformidade_reference_does_not_choose_from_multiple_people():
    from app.modules.chat.subgraphs import _filtros_conformidade
    history = [{"role": "assistant", "content": "Encontrei 2 usuário(s):\n- Maria: maria@example.com\n- Ana: ana@example.com"}]
    assert _filtros_conformidade("Como está a conformidade dela?", history) is None
    assert _filtros_conformidade("Como está a conformidade dela?", []) is None


@pytest.fixture(autouse=True)
def no_external_fetch(monkeypatch):
    monkeypatch.setattr(
        sst_tools, "get_fetch_mcp_client", lambda: UnavailableFetchClient(),
    )


@pytest.fixture
def configured_mongo(monkeypatch):
    monkeypatch.setattr(config, "MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setattr(config, "MONGODB_DATABASE", "astro")


def test_consultar_nrs_supports_multiple_numbers_and_selected_fields(
    monkeypatch, configured_mongo,
):
    collection = FakeCollection([{
        "_id": 1,
        "nome": "Disposições Gerais",
        "objetivo": "Estabelecer disposições gerais.",
        "data_criacao": datetime(2026, 9, 12, tzinfo=timezone.utc),
    }, {
        "_id": 6,
        "nome": "Equipamento de Proteção Individual",
        "objetivo": "Definir requisitos para EPI.",
    }])
    monkeypatch.setattr(sst_tools, "get_collection", lambda: collection)

    result = consultar_nrs.invoke({
        "numeros": [1, 6], "campos": ["objetivo", "data_criacao"], "limite": 2,
    })

    assert result["status"] == "ok"
    assert result["quantidade"] == 2
    assert result["nrs"][0] == {
        "numero": 1,
        "nome": "Disposições Gerais",
        "objetivo": "Estabelecer disposições gerais.",
        "data_criacao": "2026-09-12T00:00:00+00:00",
    }
    assert collection.query == {"_id": {"$in": [1, 6]}}
    assert collection.cursor.sort_args == ("_id", 1)
    assert collection.cursor.skip_value == 0
    assert collection.cursor.limit_value == 2


def test_consultar_nrs_lists_compact_fields_with_pagination(monkeypatch, configured_mongo):
    documents = [{
        "_id": number,
        "nome": f"Norma {number}",
        "revogada": False,
        "ultima_atualizacao": "01/01/2026",
        "descricao": "Texto extenso que nao deve entrar na listagem.",
    } for number in range(1, 61)]
    collection = FakeCollection(documents)
    monkeypatch.setattr(sst_tools, "get_collection", lambda: collection)

    result = consultar_nrs.invoke({
        "modo": "listar", "revogada": False, "pagina": 2, "limite": 50,
    })

    assert result["modo"] == "listar"
    assert result["quantidade"] == 10
    assert result["nrs"][0] == {
        "numero": 51,
        "nome": "Norma 51",
        "situacao": "VIGENTE",
        "ultima_atualizacao": "01/01/2026",
    }
    assert result["paginacao"] == {
        "pagina": 2,
        "limite": 50,
        "total": 60,
        "total_paginas": 2,
        "tem_proxima_pagina": False,
    }
    assert collection.projection == {
        "_id": 1, "nome": 1, "revogada": 1, "ultima_atualizacao": 1,
    }
    assert collection.cursor.skip_value == 50


def test_consultar_nrs_escapes_text_and_filters_status(monkeypatch, configured_mongo):
    collection = FakeCollection([])
    monkeypatch.setattr(sst_tools, "get_collection", lambda: collection)

    result = consultar_nrs.invoke({
        "termo": "EPI.*", "revogada": False, "usabilidade": "Funcionário",
    })

    assert result["status"] == "sem_dados"
    assert collection.query["revogada"] is False
    assert collection.query["$or"][0]["nome"].pattern == re.escape("EPI.*")
    assert collection.query["usabilidade"].pattern == "^Funcionário$"


def test_consultar_nrs_returns_unavailable_without_configuration(monkeypatch):
    monkeypatch.setattr(config, "MONGODB_URI", None)
    monkeypatch.setattr(config, "MONGODB_DATABASE", None)
    assert consultar_nrs.invoke({}) == {
        "status": "indisponivel", "mensagem": "Consulta de NRs indisponivel.",
    }


def test_consultar_nrs_hides_mongo_errors(monkeypatch, configured_mongo):
    collection = FakeCollection(error=ServerSelectionTimeoutError("host privado"))
    monkeypatch.setattr(sst_tools, "get_collection", lambda: collection)
    assert consultar_nrs.invoke({"numeros": [1]}) == {
        "status": "indisponivel", "mensagem": "Consulta de NRs indisponivel.",
    }


def test_consultar_nrs_prioritizes_official_catalog_and_keeps_astro_context(
    monkeypatch, configured_mongo,
):
    class FetchClient:
        async def fetch(self, url, **_kwargs):
            assert url == sst_tools.NR_OFFICIAL_CATALOG_URL
            return {
                "status": "ok",
                "conteudo": (
                    "Atualizado em 10/09/2026 12h00\n\n"
                    "NR-1 - DISPOSIÇÕES GERAIS E GERENCIAMENTO DE RISCOS\n\n"
                    "NR-2 - INSPEÇÃO PRÉVIA (REVOGADA)\n\n"
                    "NR-6 - EQUIPAMENTO DE PROTEÇÃO INDIVIDUAL - EPI"
                ),
            }

    collection = FakeCollection([{
        "_id": 6,
        "nome": "Nome interno antigo",
        "revogada": False,
        "ultima_atualizacao": "01/01/2025",
    }])
    monkeypatch.setattr(sst_tools, "get_collection", lambda: collection)
    monkeypatch.setattr(sst_tools, "get_fetch_mcp_client", lambda: FetchClient())

    result = consultar_nrs.invoke({"modo": "listar", "revogada": False})

    assert result["origem_principal"] == "web_oficial"
    assert [item["numero"] for item in result["nrs"]] == [1, 6]
    assert result["nrs"][1]["nome"] == "EQUIPAMENTO DE PROTEÇÃO INDIVIDUAL - EPI"
    assert result["fonte"]["protocolo"] == "MCP Fetch"
    assert result["fontes"][1]["titulo"] == "Contexto interno de NRs do Astro"


def test_consultar_nrs_uses_official_detail_before_internal_description(
    monkeypatch, configured_mongo,
):
    class FetchClient:
        async def fetch(self, url, **_kwargs):
            assert url.endswith("/norma-regulamentadora-no-6-nr-6")
            return {
                "status": "ok",
                "conteudo": (
                    "# Norma Regulamentadora No. 6 (NR-6)\n\n"
                    "Atualizado em 01/09/2025 16h20\n\n"
                    "A NR-6 regulamenta requisitos relacionados aos equipamentos de "
                    "proteção individual e às responsabilidades das organizações e "
                    "dos trabalhadores no uso desses equipamentos.\n\n"
                    "A página oficial também reúne o texto vigente e os atos normativos "
                    "que alteraram a norma ao longo do tempo para consulta pública."
                ),
            }

    collection = FakeCollection([{
        "_id": 6,
        "nome": "Equipamento de Proteção Individual",
        "objetivo": "Contexto específico cadastrado no Astro.",
    }])
    monkeypatch.setattr(sst_tools, "get_collection", lambda: collection)
    monkeypatch.setattr(sst_tools, "get_fetch_mcp_client", lambda: FetchClient())

    result = consultar_nrs.invoke({
        "numeros": [6], "modo": "detalhar", "campos": ["objetivo"],
    })

    assert result["origem_principal"] == "web_oficial"
    assert result["nrs"][0]["pagina_oficial_atualizada_em"] == "01/09/2025"
    assert "A NR-6 regulamenta" in result["nrs"][0]["resumo_oficial"]
    assert result["nrs"][0]["objetivo"] == "Contexto específico cadastrado no Astro."


def test_sst_tool_contract_accepts_empty_query_and_rejects_invalid_payload():
    decision = SstToolDecision.model_validate({
        "acao": "consultar_nrs", "filtros": None, "resposta": None,
    })
    assert decision.filtros == ConsultarNrsArgs()

    with pytest.raises(ValidationError):
        ConsultarNrsArgs(numeros=[1, 1])
    with pytest.raises(ValidationError):
        SstToolDecision.model_validate({
            "acao": "consultar_nrs",
            "filtros": {},
            "resposta": {
                "dominio": "sst", "intencao": "orientar", "status": "concluido",
                "resposta": "Texto", "recomendacao": "",
            },
        })
    public_decision = SstToolDecision.model_validate({
        "acao": "consultar_orientacoes_sst",
        "filtros": {"termo": "proteção contra quedas"},
        "resposta": None,
    })
    assert public_decision.filtros == ConsultarOrientacoesSstArgs(
        termo="proteção contra quedas",
    )


def test_sst_tool_is_registered_with_safe_schema():
    assert consultar_nrs.name == "consultar_nrs"
    assert consultar_orientacoes_sst.name == "consultar_orientacoes_sst"
    assert consultar_nrs_obrigatorias.name == "consultar_nrs_obrigatorias"
    assert consultar_situacao_nrs.name == "consultar_situacao_nrs"
    assert sst_tools.TOOLS_SST == [
        sst_tools.consultar_conformidade_usuario,
        consultar_nrs_organizacao,
        consultar_nrs, consultar_orientacoes_sst,
        consultar_nrs_obrigatorias, consultar_situacao_nrs,
    ]
    properties = consultar_nrs.args_schema.model_json_schema()["properties"]
    assert "collection" not in properties
    assert "query" not in properties
    assert consultar_nrs_obrigatorias.args_schema.model_json_schema()["properties"] == {}
    assert consultar_situacao_nrs.args_schema.model_json_schema()["properties"] == {}


@pytest.mark.parametrize("scope", ["unidade", "empresa"])
def test_organization_nrs_uses_authenticated_workspace_and_distinct_union(monkeypatch, scope):
    connection = FakePostgresConnection([
        ("Astro", "Matriz", 6, "EPI", False),
        ("Astro", "Matriz", 27, "Registro profissional", True),
    ])
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
    monkeypatch.setattr(sst_tools, "get_postgres_connection", lambda: connection)
    result = consultar_nrs_organizacao.invoke({"escopo": scope}, config={"configurable": {
        "usuario_atual": {"uid": "owner", "role": "COLABORADOR"},
    }})
    assert result["quantidade"] == 2
    assert result["escopo"] == scope
    assert result["nrs"][1]["revogada"] is True
    assert connection.db_cursor.parameters == ["owner", scope, scope]
    query = connection.db_cursor.query
    assert "SELECT DISTINCT" in query
    assert "usuario.firebase_uid = %s" in query
    assert "alvo.workspace_id = contexto.workspace_id" in query
    assert "alvo.id_unidade = contexto.id_unidade" in query
    assert "unidade_nr.unidade_id = alvo.id_unidade" in query
    assert "cargo_nr" not in query
    assert "ativo = TRUE" not in query  # Todas as unidades, não apenas as ativas.
    assert "owner" not in str(result)


@pytest.mark.parametrize("rows,status", [([], "nao_aplicavel"), ([("Astro", "Matriz", None, None, None)], "ok")])
def test_organization_nrs_handles_no_affiliation_and_no_assignments(monkeypatch, rows, status):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
    monkeypatch.setattr(sst_tools, "get_postgres_connection", lambda: FakePostgresConnection(rows))
    result = consultar_nrs_organizacao.invoke({}, config={"configurable": {
        "usuario_atual": {"uid": "owner", "role": "ADMIN"},
    }})
    assert result["status"] == status
    if rows:
        assert result["nrs"] == []
        assert result["quantidade"] == 0


def test_organization_schema_rejects_external_selectors():
    for filters in ({"workspace_id": 2}, {"uid": "someone"}, {"escopo": "todas_empresas"}):
        with pytest.raises(ValidationError):
            ConsultarNrsOrganizacaoArgs.model_validate(filters)
    decision = SstToolDecision.model_validate({"acao": "consultar_nrs_organizacao", "filtros": {"escopo": "empresa"}})
    assert isinstance(decision.filtros, ConsultarNrsOrganizacaoArgs)


def test_organization_missing_identity_and_database_failure_are_not_empty_success(monkeypatch):
    assert consultar_nrs_organizacao.invoke({})["status"] == "erro"
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
    def unavailable():
        raise RuntimeError("Banco indisponível")
    monkeypatch.setattr(sst_tools, "get_postgres_connection", unavailable)
    result = consultar_nrs_organizacao.invoke({"escopo": "empresa"}, config={"configurable": {
        "usuario_atual": {"uid": "owner", "role": "GESTOR"},
    }})
    assert result["status"] == "indisponivel"
    assert "nrs" not in result


def test_consultar_nrs_obrigatorias_uses_authenticated_user_and_current_schema(monkeypatch):
    rows = [
        ("Lucas", "Eletricista", "Unidade Centro", 10, "Segurança em Eletricidade", 24),
        ("Lucas", "Eletricista", "Unidade Centro", 18, "Construção", 12),
    ]
    connection = FakePostgresConnection(rows)
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
    monkeypatch.setattr(sst_tools, "get_postgres_connection", lambda: connection)

    result = consultar_nrs_obrigatorias.invoke(
        {},
        config={"configurable": {
            "usuario_atual": {"uid": "firebase-owner", "role": "COLABORADOR"},
        }},
    )

    assert result == {
        "status": "ok",
        "usuario": "Lucas",
        "cargo": "Eletricista",
        "unidade": "Unidade Centro",
        "quantidade": 2,
        "nrs": [
            {"numero": 10, "titulo": "Segurança em Eletricidade", "tempo_reciclagem_meses": 24},
            {"numero": 18, "titulo": "Construção", "tempo_reciclagem_meses": 12},
        ],
        "fonte": {
            "tipo": "postgresql",
            "tabelas": [
                "usuario", "cargo", "unidade", "cargo_nr", "unidade_nr", "nr_catalogo",
            ],
        },
    }
    assert connection.db_cursor.parameters == ["firebase-owner"]
    assert "FROM usuario" in connection.db_cursor.query
    assert "JOIN cargo_nr" in connection.db_cursor.query
    assert "JOIN unidade_nr" in connection.db_cursor.query
    assert "nr_catalogo.revogada = FALSE" in connection.db_cursor.query
    assert "usuarios" not in connection.db_cursor.query


def test_consultar_nrs_obrigatorias_returns_empty_assignment(monkeypatch):
    connection = FakePostgresConnection([
        ("Lucas", "Analista", "Matriz", None, None, None),
    ])
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
    monkeypatch.setattr(sst_tools, "get_postgres_connection", lambda: connection)

    result = consultar_nrs_obrigatorias.invoke(
        {},
        config={"configurable": {
            "usuario_atual": {"uid": "firebase-owner", "role": "COLABORADOR"},
        }},
    )

    assert result["status"] == "ok"
    assert result["quantidade"] == 0
    assert result["nrs"] == []


def test_consultar_nrs_obrigatorias_does_not_apply_to_admin(monkeypatch):
    monkeypatch.setattr(
        sst_tools,
        "get_postgres_connection",
        lambda: pytest.fail("admin nao deve consultar o banco"),
    )
    result = consultar_nrs_obrigatorias.invoke(
        {},
        config={"configurable": {
            "usuario_atual": {"uid": "admin-uid", "role": "ADMIN"},
        }},
    )
    assert result["status"] == "nao_aplicavel"


def test_consultar_situacao_nrs_classifies_requirements_for_authenticated_user(monkeypatch):
    reference_date = date(2026, 9, 12)
    connection = FakePostgresConnection([
        (
            "Lucas", "Eletricista", "Matriz", 10, "Segurança em Eletricidade",
            date(2027, 9, 12), None, None, "VIGENTE", "NENHUMA", reference_date,
        ),
        (
            "Lucas", "Eletricista", "Matriz", 12, "Máquinas e Equipamentos",
            date(2026, 8, 1), datetime(2026, 9, 20, 8), datetime(2026, 9, 20, 12),
            "PENDENTE", "CONCLUIR_PENDENCIA", reference_date,
        ),
        (
            "Lucas", "Eletricista", "Matriz", 18, "Construção",
            date(2026, 8, 12), None, None, "RENOVACAO_NECESSARIA", "RENOVAR",
            reference_date,
        ),
        (
            "Lucas", "Eletricista", "Matriz", 35, "Trabalho em Altura",
            None, None, None, "REALIZACAO_NECESSARIA", "REALIZAR", reference_date,
        ),
    ])
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
    monkeypatch.setattr(sst_tools, "get_postgres_connection", lambda: connection)

    result = consultar_situacao_nrs.invoke(
        {},
        config={"configurable": {
            "usuario_atual": {"uid": "firebase-owner", "role": "COLABORADOR"},
        }},
    )

    assert result["status"] == "ok"
    assert result["quantidade"] == 4
    assert result["data_referencia"] == "2026-09-12"
    assert result["nrs"][0] == {
        "numero": 10,
        "titulo": "Segurança em Eletricidade",
        "situacao": "VIGENTE",
        "acao_necessaria": "NENHUMA",
        "data_validade": "2027-09-12",
        "atividade_pendente": False,
        "data_inicio_pendencia": None,
        "data_termino_pendencia": None,
    }
    assert result["nrs"][1]["situacao"] == "PENDENTE"
    assert result["nrs"][1]["atividade_pendente"] is True
    assert result["nrs"][2]["acao_necessaria"] == "RENOVAR"
    assert result["nrs"][3]["acao_necessaria"] == "REALIZAR"
    assert connection.db_cursor.parameters == ["firebase-owner"]
    query = connection.db_cursor.query
    assert "FROM usuario" in query
    assert "FROM conformidade" in query
    assert "FROM turma_funcionario" in query
    assert "LEFT JOIN LATERAL" in query
    assert "CURRENT_DATE + 30" in query


def test_consultar_situacao_nrs_does_not_apply_to_admin(monkeypatch):
    monkeypatch.setattr(
        sst_tools,
        "get_postgres_connection",
        lambda: pytest.fail("admin nao deve consultar o banco"),
    )
    result = consultar_situacao_nrs.invoke(
        {},
        config={"configurable": {
            "usuario_atual": {"uid": "admin-uid", "role": "ADMIN"},
        }},
    )
    assert result["status"] == "nao_aplicavel"
