import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from langsmith import tracing_context
from pymongo.errors import DuplicateKeyError, ConnectionFailure
from qdrant_client import AsyncQdrantClient, models

from app.api import auth
from app.core import config
from app.core.security import CurrentUser
from app.infrastructure.database.sessions import MongoSessions, utc_now
from app.infrastructure.vectorstore.memory import SummaryVectors, COLLECTION_MEMORY
from app.main import create_app
from app.modules.chat.errors import ChatError
from app.modules.chat.schemas import ChatRequest
from app.modules.chat.service import ChatService
from memory_fakes import FakeSessions, FakeVectors
from test_chat import FakeModel


@pytest.fixture(autouse=True)
def no_traces():
    with tracing_context(enabled=False):
        yield


def service_parts(model=None):
    model = model or FakeModel("direta")
    model.replies["resumo"] = '{"resumo":"O usuário pediu orientações de RH; não houve consulta de registros."}'
    repo, vectors = FakeSessions(), FakeVectors()
    return ChatService(model, repository=repo, vectors=vectors), repo, vectors, model


def test_session_end_summary_persistence_and_idempotence():
    async def scenario():
        service, repo, vectors, model = service_parts()
        user, sid = CurrentUser(uid="owner"), uuid4()
        await service.start(sid, user)
        first_date = repo.docs[str(sid)]["iniciada_em"]
        await service.start(sid, user)
        assert repo.docs[str(sid)]["iniciada_em"] == first_date
        await service.chat(ChatRequest(message="Fale de RH", session_id=sid), user)
        result = await service.end(sid, user)
        assert result.status == "encerrada" and result.resumo_indexado
        doc = repo.docs[str(sid)]
        assert doc["_id"] == str(sid) and doc["id_user"] == "owner"
        assert [m["role"] for m in doc["mensagens"]] == ["human", "assistant"]
        assert doc["resumo"] == result.resumo
        assert vectors.points[str(sid)]["resumo"] == result.resumo
        assert doc["encerrada_em"].tzinfo is not None
        assert doc["atualizada_em"] >= first_date
        count = len(model.calls)
        assert (await service.end(sid, user)) == result
        assert len(model.calls) == count
        assert len(vectors.calls) == 1
        for operation in (service.start(sid, user), service.chat(ChatRequest(message="Oi", session_id=sid), user)):
            with pytest.raises(ChatError) as error:
                await operation
            assert error.value.status_code == 409
    asyncio.run(scenario())


def test_empty_and_missing_sessions():
    async def scenario():
        service, repo, vectors, model = service_parts()
        user, sid = CurrentUser(uid="owner"), uuid4()
        with pytest.raises(ChatError) as error:
            await service.end(sid, user)
        assert error.value.status_code == 404 and not repo.docs
        await service.start(sid, user)
        result = await service.end(sid, user)
        assert result.status == "encerrada" and result.resumo is None
        assert not result.resumo_indexado and not model.calls and not vectors.calls
    asyncio.run(scenario())


def test_qdrant_failure_keeps_summary_for_retry():
    async def scenario():
        service, repo, vectors, model = service_parts()
        user = CurrentUser(uid="owner")
        first = await service.chat(ChatRequest(message="Oi"), user)
        vectors.failure = True
        with pytest.raises(ChatError) as error:
            await service.end(first.session_id, user)
        assert error.value.status_code == 503
        doc = repo.docs[str(first.session_id)]
        assert doc["status"] == "encerrando" and doc["resumo"]
        assert not doc["resumo_indexado"] and len(doc["mensagens"]) == 2
        assert "lock_token" not in doc
        count = len(model.calls)
        restarted = ChatService(model, repository=repo, vectors=vectors)
        vectors.failure = False
        result = await restarted.end(first.session_id, user)
        assert result.resumo_indexado
        assert len(model.calls) == count
        assert len(vectors.points) == 1
    asyncio.run(scenario())


def test_resume_after_qdrant_success_but_mongo_finalization_failure():
    async def scenario():
        service, repo, vectors, model = service_parts()
        user = CurrentUser(uid="owner")
        first = await service.chat(ChatRequest(message="Oi"), user)
        original = repo.update
        async def fail_final(sid, uid, token, fields, messages=None):
            if fields.get("status") == "encerrada":
                raise ChatError(503, "Falha Mongo")
            await original(sid, uid, token, fields, messages)
        repo.update = fail_final
        with pytest.raises(ChatError):
            await service.end(first.session_id, user)
        assert len(vectors.points) == 1
        assert repo.docs[str(first.session_id)]["status"] == "encerrando"
        repo.update = original
        await service.end(first.session_id, user)
        assert len(vectors.points) == 1
        assert len([c for c in model.calls if c[0] == "resumo"]) == 1
    asyncio.run(scenario())


def test_summary_checkpoints_resume_all_chunks():
    async def scenario():
        service, repo, vectors, model = service_parts()
        user, sid = CurrentUser(uid="owner"), uuid4()
        await service.start(sid, user)
        repo.docs[str(sid)]["mensagens"] = [
            {"role": "human" if i % 2 == 0 else "assistant", "content": f"trecho-{i}:" + "x" * 3990}
            for i in range(10)
        ]
        calls, original = [], model.complete
        async def fail_second(agent, messages, **kwargs):
            calls.append(messages[-1].content)
            if len(calls) == 2:
                raise ChatError(503, "LLM indisponivel")
            return await original(agent, messages, **kwargs)
        model.complete = fail_second
        with pytest.raises(ChatError):
            await service.end(sid, user)
        offset = repo.docs[str(sid)]["resumo_ate"]
        assert 0 < offset < 10 and repo.docs[str(sid)]["resumo_parcial"]
        assert not vectors.points
        model.complete = original
        result = await service.end(sid, user)
        assert result.resumo_indexado
        successful = [json.loads(c[1][-1].content) for c in model.calls if c[0] == "resumo"]
        assert sum(len(chunk["mensagens"]) for chunk in successful) == 10
        assert successful[1]["resumo_parcial"]
    asyncio.run(scenario())


def test_router_memory_lookup_and_owner_filter():
    class RememberingModel(FakeModel):
        async def complete(self, agent, messages, **kwargs):
            if agent == "roteador":
                self.calls.append((agent, messages, False))
                results = [m for m in messages if str(m.content).startswith("RESULTADO DE buscar_historico")]
                if not results:
                    return 'MEMORY={"busca":"RH"}'
                assert "outro-usuario" not in str(results)
                assert "O usuário pediu orientações" in str(results)
                return "Conversamos sobre RH, sem consulta de registros."
            return await super().complete(agent, messages, **kwargs)
    async def scenario():
        service, repo, vectors, model = service_parts()
        user = CurrentUser(uid="owner")
        old = await service.chat(ChatRequest(message="Oi"), user)
        await service.end(old.session_id, user)
        foreign = str(uuid4())
        await repo.ensure(foreign, "other")
        repo.docs[foreign].update(status="encerrada", resumo="outro-usuario")
        # Até um índice comprometido com IDs alheios não deve expor o payload.
        vectors.search = AsyncMock(return_value=[str(old.session_id), foreign])
        remembering = RememberingModel()
        new_service = ChatService(remembering, repository=repo, vectors=vectors)
        result = await new_service.chat(ChatRequest(message="O que conversamos antes?"), user)
        assert result.agentes_chamados == [
            "guardrail_entrada", "roteador", "buscar_historico", "roteador", "guardrail_saida",
        ]
        vectors.search.assert_awaited_once_with("owner", str(result.session_id), "RH")
        assert result.resposta == "Conversamos sobre RH, sem consulta de registros."
    asyncio.run(scenario())


@pytest.mark.parametrize("reply", ['MEMORY={"busca":"RH","uid":"other"}', 'MEMORY=invalid'])
def test_router_rejects_untrusted_memory_arguments(reply):
    async def scenario():
        service, repo, vectors, model = service_parts()
        model.replies["roteador"] = reply
        with pytest.raises(ChatError) as error:
            await service.chat(ChatRequest(message="Lembre RH"), CurrentUser(uid="owner"))
        assert error.value.status_code == 502
        assert not vectors.calls
    asyncio.run(scenario())


def test_router_allows_only_one_lookup_and_blocks_before_lookup():
    async def scenario():
        service, repo, vectors, model = service_parts()
        model.replies["roteador"] = 'MEMORY={"busca":"RH"}'
        with pytest.raises(ChatError) as error:
            await service.chat(ChatRequest(message="Histórico"), CurrentUser(uid="owner"))
        assert error.value.status_code == 502 and len(vectors.calls) == 1
        vectors.calls.clear()
        model.replies["guardrail_entrada"] = json.dumps({
            "decisao": "bloquear", "motivo": "injecao_de_prompt", "mensagem": "Não posso ajudar.",
        })
        await service.chat(ChatRequest(message="Histórico"), CurrentUser(uid="owner"))
        assert not vectors.calls
    asyncio.run(scenario())


def test_recent_fallback_and_no_semantic_matches():
    async def scenario():
        service, repo, vectors, _ = service_parts()
        sid = str(uuid4())
        await repo.ensure(sid, "owner")
        repo.docs[sid].update(status="encerrada", resumo="Conversa anterior")
        current = str(uuid4())
        recent = await service.memory.search("owner", current, "")
        assert recent["origem"] == "recentes_mongodb"
        assert len(recent["conversas"]) == 1
        assert not vectors.calls
        empty = await service.memory.search("owner", current, "assunto sem match")
        assert empty["conversas"] == []
        vectors.failure = True
        fallback = await service.memory.search("owner", current, "assunto")
        assert "indisponivel" in fallback["origem"] and len(fallback["conversas"]) == 1
    asyncio.run(scenario())


def test_session_routes_authentication_and_ownership():
    service, repo, vectors, model = service_parts()
    app = create_app()
    app.state.chat_service = service
    sid = str(uuid4())
    with TestClient(app) as client:
        for action in ("iniciar", "encerrar"):
            assert client.post(f"/sessions/{sid}/{action}").status_code == 401
        app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid="owner")
        result = client.post(f"/sessions/{sid}/iniciar")
        assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
        assert result.json()["session_id"] == sid
        assert client.post("/chat/messages", json={"message": "Oi", "session_id": sid}).status_code == 200
        app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid="other")
        for action in ("iniciar", "encerrar"):
            assert client.post(f"/sessions/{sid}/{action}").status_code == 404
        assert client.post("/chat/messages", json={"message": "Oi", "session_id": sid}).status_code == 404
        app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid="owner")
        assert client.post(f"/sessions/{sid}/encerrar").json()["resumo_indexado"] is True
        assert client.post("/sessions/not-uuid/iniciar").status_code == 422
        schema = client.get("/openapi.json").json()
        assert "timezone" not in schema["components"]["schemas"]["ChatRequest"]["properties"]


def test_mongo_filters_and_atomic_updates():
    async def scenario():
        repo = MongoSessions()
        collection = SimpleNamespace(
            update_one=AsyncMock(return_value=SimpleNamespace(matched_count=1)),
            find_one=AsyncMock(return_value={"_id": "session", "id_user": "owner"}),
            find_one_and_update=AsyncMock(return_value={"_id": "session", "id_user": "owner"}),
        )
        repo.collection, repo.ready = collection, True
        await repo.ensure("session", "owner")
        args = collection.update_one.call_args
        assert args.args[0] == {"_id": "session", "id_user": "owner"}
        assert args.kwargs["upsert"]
        assert "$setOnInsert" in args.args[1]
        doc, token = await repo.acquire("session", "owner")
        query = collection.find_one_and_update.call_args.args[0]
        assert query["id_user"] == "owner" and query["_id"] == "session" and "$or" in query
        pair = [{"role": "human", "content": "Oi"}, {"role": "assistant", "content": "Olá"}]
        await repo.update("session", "owner", token, {"ultima_rota": "rh"}, messages=pair)
        args = collection.update_one.call_args.args
        assert args[0]["id_user"] == "owner" and args[0]["lock_token"] == token
        assert "$gt" in args[0]["lock_ate"]
        assert args[1]["$push"]["mensagens"]["$each"] == pair
        await repo.release("session", "owner", token)
        assert collection.update_one.call_args.args[0] == {
            "_id": "session", "id_user": "owner", "lock_token": token,
        }
        collection.update_one.side_effect = DuplicateKeyError("duplicate")
        collection.find_one.return_value = None
        with pytest.raises(ChatError) as error:
            await repo.ensure("session", "attacker")
        assert error.value.status_code == 404
        collection.find_one.side_effect = ConnectionFailure("private-uri-password")
        with pytest.raises(ChatError) as error:
            await repo.get("session", "owner")
        assert error.value.status_code == 503 and "private" not in str(error.value)
    asyncio.run(scenario())


def test_expired_lease_cannot_write_after_another_worker():
    async def scenario():
        repo = FakeSessions()
        sid = str(uuid4())
        await repo.ensure(sid, "owner")
        _, first_token = await repo.acquire(sid, "owner")
        repo.docs[sid]["lock_ate"] = utc_now() - timedelta(seconds=1)
        _, second_token = await repo.acquire(sid, "owner")
        with pytest.raises(ChatError):
            await repo.update(sid, "owner", first_token, {"resumo": "stale"})
        await repo.release(sid, "owner", first_token)
        assert repo.docs[sid]["lock_token"] == second_token
    asyncio.run(scenario())


def test_qdrant_real_sdk_local_collection_and_tenant_filter(monkeypatch):
    async def scenario():
        vectors = SummaryVectors()
        vectors.client = AsyncQdrantClient(":memory:")
        await vectors.client.create_collection(COLLECTION_MEMORY,
            vectors_config={"": models.VectorParams(size=1024, distance=models.Distance.COSINE)})
        vectors.embed = AsyncMock(return_value=[1.0] + [0.0] * 1023)
        monkeypatch.setattr(config, "QDRANT_URL", "http://localhost:6333")
        # Local Qdrant não usa índices de payload; criar para validar o contrato do SDK.
        sid, other, current = str(uuid4()), str(uuid4()), str(uuid4())
        for key, uid in ((sid, "owner"), (other, "other"), (current, "owner")):
            await vectors.upsert({"_id": key, "id_user": uid, "resumo": "Resumo",
                                  "iniciada_em": utc_now()})
        await vectors.upsert({"_id": sid, "id_user": "owner", "resumo": "Resumo atualizado",
                              "iniciada_em": utc_now()})
        assert (await vectors.client.count(COLLECTION_MEMORY)).count == 3
        assert await vectors.search("owner", current, "Resumo") == [sid]
        await vectors.close()
    asyncio.run(scenario())


def test_embeddings_http_contract_and_validation(monkeypatch):
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "fake-mistral-key")
    original_client = httpx.AsyncClient
    def handler(request):
        assert str(request.url) == "https://api.mistral.ai/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer fake-mistral-key"
        assert json.loads(request.content) == {
            "model": "mistral-embed", "input": ["Resumo"], "encoding_format": "float",
        }
        return httpx.Response(200, json={"data": [{"embedding": [1.0] * 1024}]})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(handler), **kwargs))
    assert len(asyncio.run(SummaryVectors().embed("Resumo"))) == 1024


def test_wrong_vector_dimensions_fail_without_recreation(monkeypatch):
    async def scenario():
        monkeypatch.setattr(config, "QDRANT_URL", "http://localhost:6333")
        vectors = SummaryVectors()
        vectors.client = SimpleNamespace(get_collection=AsyncMock(return_value=SimpleNamespace(
            config=SimpleNamespace(params=SimpleNamespace(vectors=models.VectorParams(
                size=768, distance=models.Distance.COSINE))), payload_schema={})))
        with pytest.raises(ChatError) as error:
            await vectors.connect()
        assert error.value.status_code == 503
        assert not vectors.ready
    asyncio.run(scenario())


def test_invalid_summary_never_reaches_qdrant():
    async def scenario():
        service, repo, vectors, model = service_parts()
        user = CurrentUser(uid="owner")
        first = await service.chat(ChatRequest(message="Oi"), user)
        model.replies["resumo"] = '{"resumo":"   "}'
        with pytest.raises(ChatError) as error:
            await service.end(first.session_id, user)
        assert error.value.status_code == 502
        doc = repo.docs[str(first.session_id)]
        assert doc["status"] == "encerrando" and not doc["resumo"]
        assert "lock_token" not in doc and not vectors.calls
    asyncio.run(scenario())


def test_chat_and_end_serialize_across_service_instances():
    async def scenario():
        first, repo, vectors, model = service_parts()
        second = ChatService(model, repository=repo, vectors=vectors)
        user, sid = CurrentUser(uid="owner"), uuid4()
        await first.start(sid, user)
        entered, release = asyncio.Event(), asyncio.Event()
        original = model.complete
        async def waiting(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        model.complete = waiting
        pending = asyncio.create_task(first.chat(ChatRequest(message="Oi", session_id=sid), user))
        await entered.wait()
        try:
            with pytest.raises(ChatError) as error:
                await second.end(sid, user)
            assert error.value.status_code == 409
            with pytest.raises(ChatError) as error:
                await second.chat(ChatRequest(message="Outra", session_id=sid), user)
            assert error.value.status_code == 409
        finally:
            release.set()
            await pending
        assert len(repo.docs[str(sid)]["mensagens"]) == 2
        assert (await second.end(sid, user)).resumo_indexado
    asyncio.run(scenario())


def test_mongo_previous_revalidates_tenant_and_closed_status():
    class Cursor:
        def sort(self, key, direction):
            assert (key, direction) == ("atualizada_em", -1)
            return self
        def limit(self, value):
            assert value == 3
            return self
        def __aiter__(self):
            return self
        async def __anext__(self):
            raise StopAsyncIteration
    def find(query, projection):
        assert query["id_user"] == "owner"
        assert query["status"] == "encerrada"
        assert query["_id"] == {"$ne": "current", "$in": ["candidate"]}
        assert projection["mensagens"] == {"$slice": -6}
        return Cursor()
    async def scenario():
        repo = MongoSessions()
        repo.collection, repo.ready = SimpleNamespace(find=find), True
        assert await repo.previous("owner", "current", ["candidate"]) == []
    asyncio.run(scenario())
