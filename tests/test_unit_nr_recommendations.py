import asyncio
import json

from fastapi.testclient import TestClient

from app.api import auth
from app.core import config
from app.core.security import CurrentUser
from app.main import create_app
from app.modules.support.nr_recommendations import CompanyNrRepository
from app.modules.support.unit_nr_recommendations import (
    AGENT_NAME,
    UnitNrRecommendationService,
    UnitNrRecommendationsResponse,
    UnitRepository,
)


TEST_USER = CurrentUser(uid="workspace-manager", role="GESTOR_WORKSPACE")


class FakeModel:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def complete(self, agent, messages, *, json_mode=False):
        self.calls.append((agent, messages, json_mode))
        return self.reply


class FakeUnits:
    def __init__(self):
        self.calls = []

    def get_context(self, unit_id, user):
        self.calls.append((unit_id, user.uid))
        return {
            "id": 3,
            "nome": "Fábrica São Paulo",
            "ativa": True,
            "empresa": "Metalúrgica Astro",
            "cnpj_cadastrado": True,
            "localizacao": {"cidade": "São Paulo", "estado": "SP", "bairro": "Centro"},
            "quantidade_usuarios_cadastrados": 14,
            "quantidade_funcionarios_ativos": 12,
            "cargos_ativos": [
                {"nome": "Soldador", "quantidade": 8},
                {"nome": "Eletricista", "quantidade": 4},
            ],
            "modalidades_ativas": [{"modalidade": "PRESENCIAL", "quantidade": 12}],
        }


class FakeCompanyNrs:
    def __init__(self):
        self.closed = False

    def list_candidates(self):
        return [{
            "numero": 5,
            "nome": "Comissão Interna de Prevenção de Acidentes e de Assédio",
            "objetivo": "Estabelecer parâmetros para a CIPA.",
            "descricao": "Requisitos relacionados à prevenção nas organizações.",
            "aplicabilidade": "Organizações conforme atividades e quantitativo.",
        }]

    def close(self):
        self.closed = True


def unit_analysis_reply():
    return json.dumps({"nrs": [{
        "numero": 5,
        "justificativa": "A unidade possui doze trabalhadores presenciais em cargos operacionais.",
        "confianca": "media",
    }]})


def test_unit_agent_uses_professional_context_without_persisting():
    model, units, nrs = FakeModel(unit_analysis_reply()), FakeUnits(), FakeCompanyNrs()
    service = UnitNrRecommendationService(model, units=units, nrs=nrs)

    result = asyncio.run(service.analyze(3, TEST_USER))

    assert result.model_dump() == {
        "unidade_id": 3,
        "unidade": "Fábrica São Paulo",
        "empresa": "Metalúrgica Astro",
        "quantidade_funcionarios_ativos": 12,
        "quantidade": 1,
        "nrs_sugeridas": [{
            "numero": 5,
            "nome": "Comissão Interna de Prevenção de Acidentes e de Assédio",
            "justificativa": "A unidade possui doze trabalhadores presenciais em cargos operacionais.",
            "confianca": "media",
        }],
    }
    assert units.calls == [(3, "workspace-manager")]
    assert model.calls[0][0] == AGENT_NAME and model.calls[0][2] is True
    payload = model.calls[0][1][-1].content
    assert "Soldador" in payload and "PRESENCIAL" in payload
    assert "cargo_nr" not in payload and "unidade_nr" not in payload


def test_unit_endpoint_requires_authentication():
    class FakeService:
        def __init__(self):
            self.calls = []

        async def analyze(self, unit_id, user):
            self.calls.append((unit_id, user.uid))
            return UnitNrRecommendationsResponse(
                unidade_id=unit_id,
                unidade="Matriz",
                empresa="Astro",
                quantidade_funcionarios_ativos=20,
                quantidade=0,
                nrs_sugeridas=[],
            )

        async def close(self):
            pass

    application = create_app()
    service = FakeService()
    application.state.unit_nr_recommendation_service = service
    with TestClient(application) as client:
        assert client.post("/support/unidades/3/nrs-sugeridas").status_code == 401
        application.dependency_overrides[auth.get_current_user] = lambda: TEST_USER
        response = client.post("/support/unidades/3/nrs-sugeridas")
        invalid = client.post("/support/unidades/0/nrs-sugeridas")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["quantidade_funcionarios_ativos"] == 20
    assert service.calls == [(3, "workspace-manager")]
    assert invalid.status_code == 422


def test_unit_repository_scopes_workspace_and_builds_context(monkeypatch):
    class Cursor:
        def __init__(self):
            self.calls = []
            self.step = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def execute(self, query, parameters):
            self.calls.append((query, parameters))
            self.step += 1

        def fetchone(self):
            if self.step == 1:
                return (3, "Matriz", True, "Astro", "12345678000199", "São Paulo", "SP", "Centro")
            return (14, 12)

        def fetchall(self):
            if self.step == 3:
                return [("Soldador", 8), ("Eletricista", 4)]
            return [("PRESENCIAL", 12)]

    class Connection:
        def __init__(self):
            self.db_cursor = Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def cursor(self):
            return self.db_cursor

    connection = Connection()

    def connect(*_args, **kwargs):
        assert "default_transaction_read_only=on" in kwargs["options"]
        return connection

    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://example")
    context = UnitRepository(connect).get_context(3, TEST_USER)

    assert context["quantidade_funcionarios_ativos"] == 12
    assert context["cargos_ativos"][0] == {"nome": "Soldador", "quantidade": 8}
    assert context["modalidades_ativas"] == [{"modalidade": "PRESENCIAL", "quantidade": 12}]
    first_query, first_parameters = connection.db_cursor.calls[0]
    assert "unidade.workspace_id" in first_query
    assert first_parameters == (3, "workspace-manager")
    assert all("INSERT" not in query.upper() and "UPDATE" not in query.upper()
               for query, _ in connection.db_cursor.calls)


def test_company_nr_repository_filters_only_company_nrs(monkeypatch):
    class Cursor(list):
        def sort(self, *_args):
            return self

        def limit(self, *_args):
            return self

    class Collection:
        def find(self, query, projection):
            self.query, self.projection = query, projection
            return Cursor([{
                "_id": 5,
                "nome": "CIPA",
                "objetivo": "Prevenção",
                "descricao": "Descrição",
                "aplicabilidade": "Empresas",
            }])

    collection = Collection()

    class Database:
        def __getitem__(self, key):
            assert key == "nrs"
            return collection

    class Mongo:
        def __getitem__(self, _key):
            return Database()

        def close(self):
            pass

    monkeypatch.setattr(config, "MONGODB_URI", "mongodb://example")
    monkeypatch.setattr(config, "MONGODB_DATABASE", "astro")
    repository = CompanyNrRepository(lambda *_args, **_kwargs: Mongo())

    assert repository.list_candidates()[0]["numero"] == 5
    assert collection.query["usabilidade"].match("Empresa")
    assert not collection.query["usabilidade"].match("Funcionario")
    assert collection.query["revogada"] == {"$ne": True}
