import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.api import auth
from app.core import config
from app.core.security import CurrentUser
from app.main import create_app
from app.modules.chat.errors import InvalidAgentResponse
from app.modules.support.nr_recommendations import (
    AGENT_NAME,
    EmployeeNrRepository,
    PositionNrRecommendationService,
    PositionNrRecommendationsResponse,
    PositionRepository,
)


class FakeModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def complete(self, agent, messages, *, json_mode=False):
        self.calls.append((agent, messages, json_mode))
        return self.replies.pop(0)


class FakePositions:
    def __init__(self, position=None):
        self.position = position or {"id": 7, "nome": "Eletricista"}
        self.calls = []

    def get(self, position_id, user):
        self.calls.append((position_id, user.uid))
        return self.position


class FakeNrs:
    def __init__(self):
        self.calls = 0
        self.closed = False

    def list_candidates(self):
        self.calls += 1
        return [
            {
                "numero": 10,
                "nome": "Segurança em Instalações e Serviços em Eletricidade",
                "objetivo": "Proteger trabalhadores em atividades com eletricidade.",
                "descricao": "Requisitos de segurança em serviços elétricos.",
                "aplicabilidade": "Trabalhos com instalações elétricas.",
            },
            {
                "numero": 35,
                "nome": "Trabalho em Altura",
                "objetivo": "Proteger trabalhadores em altura.",
                "descricao": "Requisitos para trabalho em altura.",
                "aplicabilidade": "Atividades acima do nível inferior.",
            },
        ]

    def close(self):
        self.closed = True


def analysis_reply(number=10):
    return json.dumps({"nrs": [{
        "numero": number,
        "justificativa": "O cargo indica atuação direta com instalações elétricas.",
        "confianca": "alta",
    }]})


TEST_USER = CurrentUser(uid="manager", role="GESTOR")


def test_isolated_agent_suggests_only_candidates_without_persisting():
    model, positions, nrs = FakeModel([analysis_reply()]), FakePositions(), FakeNrs()
    service = PositionNrRecommendationService(model, positions=positions, nrs=nrs)

    result = asyncio.run(service.analyze(7, TEST_USER))

    assert result.model_dump() == {
        "cargo_id": 7,
        "cargo": "Eletricista",
        "quantidade": 1,
        "nrs_sugeridas": [{
            "numero": 10,
            "nome": "Segurança em Instalações e Serviços em Eletricidade",
            "justificativa": "O cargo indica atuação direta com instalações elétricas.",
            "confianca": "alta",
        }],
    }
    assert positions.calls == [(7, "manager")] and nrs.calls == 1
    assert model.calls[0][0] == AGENT_NAME and model.calls[0][2] is True
    assert "cargo_nr" not in model.calls[0][1][-1].content


def test_isolated_agent_retries_invalid_contract_once():
    model = FakeModel(["resposta fora do contrato", analysis_reply()])
    service = PositionNrRecommendationService(
        model, positions=FakePositions(), nrs=FakeNrs(),
    )

    result = asyncio.run(service.analyze(7, TEST_USER))

    assert result.quantidade == 1
    assert len(model.calls) == 2


def test_isolated_agent_rejects_nr_outside_authorized_candidates():
    service = PositionNrRecommendationService(
        FakeModel([analysis_reply(99), analysis_reply(99)]),
        positions=FakePositions(), nrs=FakeNrs(),
    )

    with pytest.raises(InvalidAgentResponse) as error:
        asyncio.run(service.analyze(7, TEST_USER))

    assert error.value.stage == AGENT_NAME


def test_isolated_agent_does_not_run_without_employee_nr_candidates():
    model = FakeModel([analysis_reply()])
    nrs = FakeNrs()
    nrs.list_candidates = lambda: []
    service = PositionNrRecommendationService(
        model, positions=FakePositions(), nrs=nrs,
    )

    result = asyncio.run(service.analyze(7, TEST_USER))

    assert result.quantidade == 0 and result.nrs_sugeridas == []
    assert model.calls == []


def test_support_endpoint_requires_authentication_and_returns_analysis():
    class FakeService:
        def __init__(self):
            self.calls = []

        async def analyze(self, position_id, user):
            self.calls.append((position_id, user.uid))
            return PositionNrRecommendationsResponse(
                cargo_id=position_id,
                cargo="Eletricista",
                quantidade=0,
                nrs_sugeridas=[],
            )

        async def close(self):
            pass

    application = create_app()
    service = FakeService()
    application.state.nr_recommendation_service = service
    with TestClient(application) as client:
        assert client.post("/support/cargos/7/nrs-sugeridas").status_code == 401
        application.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(
            uid="manager", role="GESTOR",
        )
        response = client.post("/support/cargos/7/nrs-sugeridas")
        invalid = client.post("/support/cargos/0/nrs-sugeridas")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["cargo_id"] == 7
    assert service.calls == [(7, "manager")]
    assert invalid.status_code == 422


def test_position_repository_uses_parameterized_read_only_query(monkeypatch):
    class Cursor:
        def __init__(self):
            self.query = None
            self.parameters = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def execute(self, query, parameters):
            self.query, self.parameters = query, parameters

        def fetchone(self):
            return (7, "Eletricista")

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
    connect_calls = []

    def connect(*args, **kwargs):
        connect_calls.append((args, kwargs))
        return connection

    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://example")
    result = PositionRepository(connect).get(7, TEST_USER)

    assert result == {"id": 7, "nome": "Eletricista"}
    assert connection.db_cursor.parameters == (7, "manager")
    assert "%s" in connection.db_cursor.query
    assert "cargo.workspace_id" in connection.db_cursor.query
    assert "default_transaction_read_only=on" in connect_calls[0][1]["options"]


def test_employee_nr_repository_applies_fixed_filters(monkeypatch):
    class Cursor(list):
        def sort(self, *_args):
            return self

        def limit(self, *_args):
            return self

    class Collection:
        def __init__(self):
            self.query = self.projection = None

        def find(self, query, projection):
            self.query, self.projection = query, projection
            return Cursor([{
                "_id": 10,
                "nome": "NR-10",
                "objetivo": "Segurança elétrica",
                "descricao": "Descrição",
                "aplicabilidade": "Eletricidade",
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
    repository = EmployeeNrRepository(lambda *_args, **_kwargs: Mongo())

    candidates = repository.list_candidates()

    assert candidates[0]["numero"] == 10
    assert collection.query["revogada"] == {"$ne": True}
    assert collection.query["usabilidade"].match("Funcionario")
    assert collection.query["usabilidade"].match("Funcionário")
    assert set(collection.projection) == {
        "_id", "nome", "objetivo", "descricao", "aplicabilidade",
    }
