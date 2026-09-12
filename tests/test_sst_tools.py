from datetime import datetime, timezone
import re

import pytest
from pydantic import ValidationError
from pymongo.errors import ServerSelectionTimeoutError

from app.core import config
from app.modules.sst import tools as sst_tools
from app.modules.sst.tools import ConsultarNrsArgs, SstToolDecision, consultar_nrs


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
    assert sst_tools.TOOLS_SST == [consultar_nrs]
    properties = consultar_nrs.args_schema.model_json_schema()["properties"]
    assert "collection" not in properties
    assert "query" not in properties
