"""Contratos do histórico e retomada de sessões para web e mobile (SCRUM-453)."""

import asyncio
import base64
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import ConnectionFailure

from app.api import auth
from app.core import config
from app.core.security import CurrentUser
from app.infrastructure.database.sessions import MongoSessions, utc_now
from app.main import create_app
from app.modules.chat.errors import ChatError
from app.modules.chat.service import ChatService
from app.modules.chat.session_listing import compact_session_text, encode_session_cursor
from memory_fakes import FakeAccessRoles, FakeFaqVectors, FakeSessions, FakeVectors
from test_chat import FakeModel


@pytest.fixture
def sessions_client():
    application = create_app()
    model = FakeModel("direta")
    model.replies["resumo"] = '{"resumo":"Conversa sobre orientações."}'
    repository = FakeSessions()
    application.state.chat_service = ChatService(
        model, repository=repository, vectors=FakeVectors(), faq_vectors=FakeFaqVectors(),
    )
    application.state.access_roles = FakeAccessRoles()
    application.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(
        uid="owner", role="COLABORADOR",
    )
    with TestClient(application) as client:
        yield client, repository, application, model


def seed_session(repository, number=1, *, uid="owner", updated_at=None, status="ativa", messages=None):
    sid = str(UUID(int=number))
    created = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    repository.docs[sid] = {
        "_id": sid, "id_user": uid, "iniciada_em": created,
        "atualizada_em": updated_at or created, "status": status,
        "mensagens": messages or [], "resumo": "", "resumo_indexado": False,
    }
    return sid


def test_list_empty_and_read_without_side_effects(sessions_client):
    client, repo, _, model = sessions_client
    response = client.get("/sessions")
    assert response.status_code == 200
    assert response.json() == {"sessions": [], "next_cursor": None}
    assert response.headers["cache-control"] == "no-store"
    sid = seed_session(repo, status="encerrada")
    before = deepcopy(repo.docs)
    assert client.get("/sessions").json()["sessions"][0] == {
        "session_id": sid, "title": "Nova conversa", "last_message_preview": "",
        "created_at": "2026-10-08T12:00:00Z", "updated_at": "2026-10-08T12:00:00Z",
        "status": "encerrada",
    }
    assert client.get(f"/sessions/{sid}/messages").json()["status"] == "encerrada"
    assert repo.docs == before and not model.calls


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic invalid"},
                                      {"Authorization": "Bearer invalid"}])
def test_sessions_require_existing_firebase_auth(sessions_client, monkeypatch, headers):
    client, repo, app, model = sessions_client
    app.dependency_overrides.clear()
    def invalid_token(_token):
        raise auth.auth.InvalidIdTokenError("Invalid token")
    monkeypatch.setattr(auth, "verify_firebase_id_token", invalid_token)
    sid = str(uuid4())
    for method, path in [("get", "/sessions"), ("get", f"/sessions/{sid}/messages"),
                         ("post", f"/sessions/{sid}/iniciar"), ("post", f"/sessions/{sid}/encerrar")]:
        response = getattr(client, method)(path, headers=headers)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
    assert not repo.docs and not model.calls


def test_token_uid_controls_listing_without_additional_login(sessions_client, monkeypatch):
    client, repo, app, _ = sessions_client
    own = seed_session(repo, 1)
    other = seed_session(repo, 2, uid="other")
    app.dependency_overrides.clear()
    monkeypatch.setattr(auth, "verify_firebase_id_token", lambda token: {"uid": token})
    assert client.get("/sessions", headers={"Authorization": "Bearer owner"}).json()["sessions"][0]["session_id"] == own
    assert client.get("/sessions", headers={"Authorization": "Bearer other"}).json()["sessions"][0]["session_id"] == other
    assert app.state.access_roles.calls == ["owner", "other"]
    app.state.access_roles.role = None
    assert client.get("/sessions", headers={"Authorization": "Bearer owner"}).status_code == 403


def test_all_endpoints_hide_foreign_session(sessions_client):
    client, repo, app, _ = sessions_client
    sent = client.post("/chat/messages", json={"message": "Primeira pergunta"}).json()
    sid = sent["session_id"]
    before = deepcopy(repo.docs)
    app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid="other", role="COLABORADOR")
    assert client.get("/sessions").json() == {"sessions": [], "next_cursor": None}
    assert client.get(f"/sessions/{sid}/messages").status_code == 404
    assert client.post(f"/sessions/{sid}/iniciar").status_code == 404
    assert client.post(f"/sessions/{sid}/encerrar").status_code == 404
    assert client.post("/chat/messages", json={"message": "Nova", "session_id": sid}).status_code == 404
    assert repo.docs == before


def test_cursor_pagination_has_stable_tie_breaker_and_includes_closed(sessions_client):
    client, repo, _, _ = sessions_client
    date = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    ids = [seed_session(repo, i, updated_at=date + timedelta(minutes=i // 2),
                        status="encerrada" if i % 2 else "ativa") for i in range(1, 6)]
    seed_session(repo, 99, uid="other", updated_at=date + timedelta(days=1))
    found, cursor = [], None
    for expected in ([ids[4], ids[3]], [ids[2], ids[1]], [ids[0]]):
        params = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        response = client.get("/sessions", params=params)
        assert response.status_code == 200
        body = response.json()
        assert [session["session_id"] for session in body["sessions"]] == expected
        assert body == client.get("/sessions", params=params).json()
        found.extend(expected)
        cursor = body["next_cursor"]
    assert cursor is None and len(found) == len(set(found)) == 5


def test_default_and_maximum_limits(sessions_client):
    client, repo, _, _ = sessions_client
    for i in range(1, 22):
        seed_session(repo, i)
    page = client.get("/sessions").json()
    assert len(page["sessions"]) == 20 and page["next_cursor"]
    final = client.get("/sessions", params={"cursor": page["next_cursor"]}).json()
    assert len(final["sessions"]) == 1 and final["next_cursor"] is None
    assert len(client.get("/sessions?limit=100").json()["sessions"]) == 21


@pytest.mark.parametrize("query", ["limit=0", "limit=-1", "limit=101", "limit=abc",
                                  "limit=1.5", "cursor=", "uid=other", "id_user=other",
                                  "cursor=" + "x" * 1025])
def test_invalid_query_is_rejected(sessions_client, query):
    client, _, _, _ = sessions_client
    assert client.get("/sessions?" + query).status_code == 422


@pytest.mark.parametrize("cursor", ["not-a-cursor", "%", "YQ", "e30", "bnVsbA"])
def test_invalid_cursor_is_rejected_before_repository_access(sessions_client, monkeypatch, cursor):
    client, repo, _, _ = sessions_client
    listing = AsyncMock()
    monkeypatch.setattr(repo, "list_sessions", listing)
    result = client.get("/sessions", params={"cursor": cursor})
    assert result.status_code == 400
    assert result.json() == {"detail": "Cursor invalido para esta consulta."}
    listing.assert_not_called()


def test_cursor_cannot_be_reused_by_another_user(sessions_client):
    client, repo, app, _ = sessions_client
    seed_session(repo, 1)
    seed_session(repo, 2)
    cursor = client.get("/sessions?limit=1").json()["next_cursor"]
    app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid="other", role="COLABORADOR")
    assert client.get("/sessions", params={"cursor": cursor}).status_code == 400


@pytest.mark.parametrize("mutation", [{"version": 2}, {"updated_at": "2026-10-08T12:00:00"},
                                      {"session_id": "invalid"}, {"extra": "value"}])
def test_cursor_payload_validation(sessions_client, mutation):
    client, _, _, _ = sessions_client
    cursor = encode_session_cursor(utc_now(), uuid4(), "owner")
    payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    payload.update(mutation)
    invalid = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    assert client.get("/sessions", params={"cursor": invalid}).status_code == 400


def test_legacy_title_preview_and_utc_dates(sessions_client):
    client, repo, _, _ = sessions_client
    sid = seed_session(repo, messages=[
        {"role": "human", "content": "# NRs para **minha unidade**"},
        {"role": "assistant", "content": "Orientação anterior"},
        {"role": "human", "content": "Segunda pergunta"},
        {"role": "assistant", "content": "## Vamos **conferir**\n\n[atividades](https://example.com) da `unidade`."},
    ])
    repo.docs[sid]["atualizada_em"] = datetime(2026, 10, 8, 9, 5, tzinfo=timezone(timedelta(hours=-3)))
    repo.docs[sid]["iniciada_em"] = datetime(2026, 10, 8, 12)  # BSON legado é UTC.
    repo.docs[sid].pop("status")
    before = deepcopy(repo.docs)
    session = client.get("/sessions").json()["sessions"][0]
    assert session["title"] == "NRs para minha unidade"
    assert session["last_message_preview"] == "Vamos conferir atividades da unidade."
    assert session["created_at"] == "2026-10-08T12:00:00Z"
    assert session["updated_at"] == "2026-10-08T12:05:00Z" and session["status"] == "ativa"
    assert repo.docs == before


def test_title_and_preview_are_updated_with_persisted_messages(sessions_client):
    client, repo, _, model = sessions_client
    model.replies["roteador"] = "**Resposta inicial**"
    first = client.post("/chat/messages", json={"message": "Primeira pergunta **importante**"}).json()
    sid = first["session_id"]
    doc = repo.docs[sid]
    date = doc["atualizada_em"]
    assert doc["titulo"] == "Primeira pergunta importante"
    assert doc["ultima_mensagem_previa"] == "Resposta inicial"
    model.replies["roteador"] = "# Nova resposta\nConfira [a orientação](https://example.com)."
    second = client.post("/chat/messages", json={"message": "Segunda pergunta", "session_id": sid})
    assert second.status_code == 200
    assert doc["atualizada_em"] > date
    assert doc["titulo"] == "Primeira pergunta importante"
    assert doc["ultima_mensagem_previa"] == "Nova resposta Confira a orientação."
    listed = client.get("/sessions").json()["sessions"][0]
    assert listed["title"] == doc["titulo"] and listed["last_message_preview"] == doc["ultima_mensagem_previa"]
    assert datetime.fromisoformat(listed["updated_at"]) == doc["atualizada_em"]
    assert len(doc["mensagens"]) == 4


def test_new_message_moves_session_to_front_and_blocked_turn_does_not_change_preview(sessions_client):
    client, repo, _, model = sessions_client
    first = client.post("/chat/messages", json={"message": "Primeira pergunta"}).json()["session_id"]
    second = client.post("/chat/messages", json={"message": "Outra pergunta"}).json()["session_id"]
    assert client.get("/sessions").json()["sessions"][0]["session_id"] == second
    assert client.post("/chat/messages", json={"message": "Continue", "session_id": first}).status_code == 200
    assert client.get("/sessions").json()["sessions"][0]["session_id"] == first
    before = deepcopy(repo.docs[first])
    model.replies["guardrail_entrada"] = '{"decisao":"bloquear","motivo":"injecao_de_prompt","mensagem":"Não posso ajudar."}'
    assert client.post("/chat/messages", json={"message": "Pedido indevido", "session_id": first}).status_code == 200
    assert repo.docs[first] == before


def test_metadata_bounds_and_plain_text():
    assert compact_session_text("**" + "a" * 300 + "**", 80) == "a" * 79 + "…"
    assert len(compact_session_text("b" * 400, 200)) == 200
    assert compact_session_text("[google_calendar_connect](https://example.com)\n- *Texto*", 200) == "google_calendar_connect • Texto"


def test_resume_preserves_uuid_history_and_refreshes_summary(sessions_client):
    client, repo, app, model = sessions_client
    model.replies["resumo"] = ['{"resumo":"Resumo inicial."}', '{"resumo":"Resumo com continuação."}']
    sid = client.post("/chat/messages", json={"message": "Primeira pergunta"}).json()["session_id"]
    created = repo.docs[sid]["iniciada_em"]
    assert client.post(f"/sessions/{sid}/encerrar").status_code == 200
    before = deepcopy(repo.docs[sid])
    history = client.get(f"/sessions/{sid}/messages").json()
    assert history["status"] == "encerrada" and history["total"] == 2
    assert repo.docs[sid] == before
    rejected = client.post("/chat/messages", json={"message": "Continue", "session_id": sid})
    assert rejected.status_code == 409 and "/iniciar" in rejected.json()["detail"]
    assert repo.docs[sid] == before
    resumed = client.post(f"/sessions/{sid}/iniciar")
    assert resumed.status_code == 200
    assert resumed.json() == {"session_id": sid, "status": "ativa", "resumo": None, "resumo_indexado": False}
    assert repo.docs[sid]["mensagens"] == before["mensagens"]
    assert repo.docs[sid]["iniciada_em"] == created
    assert repo.docs[sid]["resumo"] == "" and not repo.docs[sid]["resumo_indexado"]
    active = deepcopy(repo.docs[sid])
    assert client.post(f"/sessions/{sid}/iniciar").status_code == 200
    assert repo.docs[sid] == active
    assert client.post("/chat/messages", json={"message": "Segunda pergunta", "session_id": sid}).status_code == 200
    loaded = client.get(f"/sessions/{sid}/messages").json()
    assert loaded["session_id"] == sid and loaded["total"] == 4 and loaded["status"] == "ativa"
    assert loaded["mensagens"][:2] == history["mensagens"]
    assert repo.docs[sid]["titulo"] == "Primeira pergunta"
    ended = client.post(f"/sessions/{sid}/encerrar")
    assert ended.json()["resumo"] == "Resumo com continuação."
    summary_calls = [call for call in model.calls if call[0] == "resumo"]
    assert len(summary_calls) == 2
    assert "Primeira pergunta" in summary_calls[-1][1][-1].content
    assert "Segunda pergunta" in summary_calls[-1][1][-1].content
    assert len(app.state.chat_service.vectors.points) == 1 and len(repo.docs) == 1


def test_cannot_resume_during_end_or_another_operation(sessions_client):
    client, repo, _, _ = sessions_client
    sid = seed_session(repo, status="encerrando")
    before = deepcopy(repo.docs[sid])
    assert client.post(f"/sessions/{sid}/iniciar").status_code == 409
    assert repo.docs[sid] == before
    assert client.get(f"/sessions/{sid}/messages").json()["status"] == "encerrando"
    assert client.get("/sessions").json()["sessions"][0]["status"] == "encerrando"
    repo.docs[sid].update(status="encerrada", lock_token="another-worker", lock_ate=utc_now() + timedelta(minutes=1))
    before = deepcopy(repo.docs[sid])
    assert client.post(f"/sessions/{sid}/iniciar").status_code == 409
    assert repo.docs[sid] == before


def test_openapi_documents_sessions_contract_and_errors(sessions_client):
    client, _, _, _ = sessions_client
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    route = schema["paths"]["/sessions"]["get"]
    assert route["security"] == [{"HTTPBearer": []}]
    params = {param["name"]: param for param in route["parameters"]}
    assert set(params) == {"limit", "cursor"}
    assert params["limit"]["schema"]["default"] == 20
    assert params["limit"]["schema"]["minimum"] == 1 and params["limit"]["schema"]["maximum"] == 100
    assert {"200", "400", "401", "403", "422", "429", "503", "504"} <= route["responses"].keys()
    models = schema["components"]["schemas"]
    assert models["SessionListResponse"]["examples"]
    assert models["SessionSummary"]["properties"]["updated_at"]["format"] == "date-time"
    assert "retomar" in schema["paths"]["/sessions/{session_id}/iniciar"]["post"]["description"]
    assert "409" in schema["paths"]["/chat/messages"]["post"]["responses"]


def test_mongo_listing_uses_owner_keyset_order_and_bounded_projection():
    class Cursor:
        def __aiter__(self):
            return self
        async def __anext__(self):
            raise StopAsyncIteration
    async def scenario():
        repo = MongoSessions()
        collection = SimpleNamespace(aggregate=AsyncMock(return_value=Cursor()))
        repo.collection, repo.ready = collection, True
        date, sid = utc_now(), str(uuid4())
        assert await repo.list_sessions("owner", 20, (date, sid)) == []
        pipeline = collection.aggregate.call_args.args[0]
        assert pipeline[0] == {"$match": {"id_user": "owner", "$or": [
            {"atualizada_em": {"$lt": date}}, {"atualizada_em": date, "_id": {"$lt": sid}},
        ]}}
        assert pipeline[1] == {"$sort": {"atualizada_em": -1, "_id": -1}}
        assert pipeline[2] == {"$limit": 21}
        projection = pipeline[3]["$project"]
        assert "mensagens" not in projection and "primeira_pergunta" in projection and "ultima_mensagem" in projection
        await repo.list_sessions("owner", 100)
        assert collection.aggregate.call_args.args[0][0] == {"$match": {"id_user": "owner"}}
        assert collection.aggregate.call_args.args[0][2] == {"$limit": 101}
        collection.aggregate.side_effect = ConnectionFailure("private-mongo-uri")
        with pytest.raises(ChatError) as error:
            await repo.list_sessions("owner", 20)
        assert error.value.status_code == 503 and "private" not in error.value.detail
    asyncio.run(scenario())


def test_mongo_initializes_listing_index(monkeypatch):
    monkeypatch.setattr(config, "MONGODB_URI", "mongodb://unused")
    monkeypatch.setattr(config, "MONGODB_DATABASE", "unused")
    async def scenario():
        repo = MongoSessions()
        repo.client = SimpleNamespace()
        repo.collection = SimpleNamespace(create_index=AsyncMock())
        await repo.connect()
        assert any(call.args[0] == [("id_user", 1), ("atualizada_em", -1), ("_id", -1)]
                   for call in repo.collection.create_index.call_args_list)
    asyncio.run(scenario())
