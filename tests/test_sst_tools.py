from datetime import date, datetime, timezone
import re

import pytest
from pydantic import ValidationError
from pymongo.errors import ServerSelectionTimeoutError

from app.core import config
from app.modules.sst import tools as sst_tools
from app.modules.sst.tools import ConsultarNrsArgs, SstToolDecision, consultar_nrs
from app.modules.sst.tools import consultar_nrs_obrigatorias, consultar_situacao_nrs


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


def test_sst_tool_is_registered_with_safe_schema():
    assert consultar_nrs.name == "consultar_nrs"
    assert consultar_nrs_obrigatorias.name == "consultar_nrs_obrigatorias"
    assert consultar_situacao_nrs.name == "consultar_situacao_nrs"
    assert sst_tools.TOOLS_SST == [
        consultar_nrs, consultar_nrs_obrigatorias, consultar_situacao_nrs,
    ]
    properties = consultar_nrs.args_schema.model_json_schema()["properties"]
    assert "collection" not in properties
    assert "query" not in properties
    assert consultar_nrs_obrigatorias.args_schema.model_json_schema()["properties"] == {}
    assert consultar_situacao_nrs.args_schema.model_json_schema()["properties"] == {}


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
            "usuario_atual": {"uid": "firebase-owner", "role": "FUNCIONARIO"},
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
            "usuario_atual": {"uid": "firebase-owner", "role": "FUNCIONARIO"},
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
            "usuario_atual": {"uid": "firebase-owner", "role": "FUNCIONARIO"},
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
