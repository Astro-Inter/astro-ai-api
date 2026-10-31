"""Integração real do MemorySaver com histórico Mongo simulado e LLMs simulados."""

import asyncio
import json
from uuid import uuid4

import pytest

from app.core.security import CurrentUser
from app.modules.chat.errors import ChatError
from app.modules.chat.schemas import ChatRequest
from app.modules.chat.service import ChatService
from memory_fakes import FakeFaqVectors, FakeSessions, FakeVectors
from test_chat import FakeModel


def service_parts():
    model, repository = FakeModel("direta"), FakeSessions()
    service = ChatService(
        model, repository=repository, vectors=FakeVectors(), faq_vectors=FakeFaqVectors(),
    )
    return service, model, repository, CurrentUser(uid="owner", role="COLABORADOR")


def run(scenario):
    asyncio.run(scenario())


def test_checkpoint_contains_only_committed_public_turns_without_duplicates():
    async def scenario():
        service, model, repository, user = service_parts()
        first = await service.chat(ChatRequest(message="Primeiro assunto"), user)
        sid = str(first.session_id)
        config = service.checkpoints.config(user.uid, sid)
        snapshot = await service.graph.aget_state(config)
        assert set(snapshot.values) == {"session_id", "messages"}
        assert [m.content for m in snapshot.values["messages"]] == ["Primeiro assunto", "\n\n".join(b.conteudo for b in first.blocos if b.tipo == "texto")]
        assert snapshot.next == ()
        assert len(list(service.checkpoints.saver.list(config))) == 1

        model.calls.clear()
        second = await service.chat(ChatRequest(message="Continue", session_id=first.session_id), user)
        snapshot = await service.graph.aget_state(config)
        contents = [m.content for m in snapshot.values["messages"]]
        assert contents == ["Primeiro assunto", "\n\n".join(b.conteudo for b in first.blocos if b.tipo == "texto"), "Continue", "\n\n".join(b.conteudo for b in second.blocos if b.tipo == "texto")]
        assert len(contents) == len(repository.docs[sid]["mensagens"]) == 4
        assert len({m.id for m in snapshot.values["messages"]}) == 4
        assert len(list(service.checkpoints.saver.list(config))) == 1
        router_messages = next(call[1] for call in model.calls if call[0] == "roteador")
        assert sum(m.content == "Primeiro assunto" for m in router_messages) == 1

    run(scenario)


def test_native_subgraphs_inherit_checkpointer_and_share_conversation():
    async def scenario():
        service, model, _, user = service_parts()
        first = await service.chat(ChatRequest(message="Assunto de ontem"), user)
        model.route = "rh"
        namespaces = []
        original = service.checkpoints.saver.aput

        async def recording_put(config, checkpoint, metadata, versions):
            namespaces.append(config["configurable"].get("checkpoint_ns", ""))
            return await original(config, checkpoint, metadata, versions)

        service.checkpoints.saver.aput = recording_put
        model.calls.clear()
        await service.chat(ChatRequest(message="Explique RH", session_id=first.session_id), user)
        assert any(namespace.startswith("rh:") for namespace in namespaces)
        rh_messages = next(call[1] for call in model.calls if call[0] == "rh")
        assert any(m.content == "Assunto de ontem" for m in rh_messages)

    run(scenario)


def test_mongo_reconciles_stale_checkpoint_after_another_worker_writes():
    async def scenario():
        service, model, repository, user = service_parts()
        first = await service.chat(ChatRequest(message="Primeiro turno"), user)
        sid = str(first.session_id)
        worker = ChatService(FakeModel("direta"), repository=repository, vectors=FakeVectors())
        await worker.chat(ChatRequest(message="Atualização em outro worker", session_id=first.session_id), user)
        model.calls.clear()
        await service.chat(ChatRequest(message="Continue aqui", session_id=first.session_id), user)
        router_messages = next(call[1] for call in model.calls if call[0] == "roteador")
        assert sum(m.content == "Atualização em outro worker" for m in router_messages) == 1
        snapshot = await service.graph.aget_state(service.checkpoints.config(user.uid, sid))
        assert len(snapshot.values["messages"]) == 6

        # Um processo novo não possui checkpoints, mas reconstrói pelo Mongo.
        restarted = ChatService(FakeModel("direta"), repository=repository, vectors=FakeVectors())
        assert not (await restarted.graph.aget_state(restarted.checkpoints.config(user.uid, sid))).values
        await restarted.chat(ChatRequest(message="Após reinício", session_id=first.session_id), user)
        snapshot = await restarted.graph.aget_state(restarted.checkpoints.config(user.uid, sid))
        assert len(snapshot.values["messages"]) == 8

    run(scenario)


def test_thread_identity_is_bound_to_authenticated_user_and_session():
    async def scenario():
        service, model, _, user = service_parts()
        first = await service.chat(ChatRequest(message="Conversa própria"), user)
        sid = str(first.session_id)
        own = service.checkpoints.config(user.uid, sid)
        other = service.checkpoints.config("someone-else", sid)
        assert own != other
        assert user.uid not in own["configurable"]["thread_id"]
        assert own != service.checkpoints.config(user.uid, str(uuid4()))
        model.calls.clear()
        with pytest.raises(ChatError) as error:
            await service.chat(ChatRequest(message="Acessar", session_id=first.session_id),
                               CurrentUser(uid="someone-else", role="COLABORADOR"))
        assert error.value.status_code == 404
        assert model.calls == []
        assert not (await service.graph.aget_state(other)).values
        assert (await service.graph.aget_state(own)).values

    run(scenario)


def test_blocked_turn_and_persistence_failure_leave_no_local_memory():
    async def scenario():
        service, model, repository, user = service_parts()
        first = await service.chat(ChatRequest(message="Turno confirmado"), user)
        sid = str(first.session_id)
        config = service.checkpoints.config(user.uid, sid)
        model.replies["guardrail_entrada"] = json.dumps({
            "decisao": "bloquear", "motivo": "injecao_de_prompt", "mensagem": "Pedido bloqueado.",
        })
        await service.chat(ChatRequest(message="Conteúdo bloqueado", session_id=first.session_id), user)
        assert not (await service.graph.aget_state(config)).values
        assert len(repository.docs[sid]["mensagens"]) == 2

        model.replies.clear()
        original = repository.update

        async def unavailable(*args, **kwargs):
            if kwargs.get("messages"):
                raise ChatError(503, "Mongo indisponível.")
            return await original(*args, **kwargs)

        repository.update = unavailable
        with pytest.raises(ChatError):
            await service.chat(ChatRequest(message="Turno sem persistência", session_id=first.session_id), user)
        assert not (await service.graph.aget_state(config)).values
        assert len(repository.docs[sid]["mensagens"]) == 2
        repository.update = original
        await service.chat(ChatRequest(message="Retomada", session_id=first.session_id), user)
        snapshot = await service.graph.aget_state(config)
        assert len(snapshot.values["messages"]) == 4
        assert not any("sem persistência" in m.content for m in snapshot.values["messages"])

    run(scenario)


def test_checkpoint_failure_after_commit_keeps_successful_mongo_response():
    async def scenario():
        service, _, repository, user = service_parts()

        async def unavailable(*args, **kwargs):
            raise RuntimeError("Falha de memória local")

        service.graph.aupdate_state = unavailable
        response = await service.chat(ChatRequest(message="Olá"), user)
        sid = str(response.session_id)
        assert len(repository.docs[sid]["mensagens"]) == 2
        assert not (await service.graph.aget_state(service.checkpoints.config(user.uid, sid))).values

    run(scenario)


def test_checkpoint_eviction_and_session_end_preserve_mongo(monkeypatch):
    async def scenario():
        service, model, repository, user = service_parts()
        clock = [1000.0]
        monkeypatch.setattr(service.checkpoints, "_clock", lambda: clock[0])
        first = await service.chat(ChatRequest(message="Olá"), user)
        sid = str(first.session_id)
        clock[0] += service.checkpoints.idle_seconds + 1
        await service.chat(ChatRequest(message="Nova conversa"), user)
        assert not (await service.graph.aget_state(service.checkpoints.config(user.uid, sid))).values
        assert len(repository.docs[sid]["mensagens"]) == 2
        await service.chat(ChatRequest(message="Retomar", session_id=first.session_id), user)
        model.replies["resumo"] = '{"resumo":"Conversa de orientação."}'
        await service.end(first.session_id, user)
        assert not (await service.graph.aget_state(service.checkpoints.config(user.uid, sid))).values
        assert repository.docs[sid]["status"] == "encerrada"
        assert len(repository.docs[sid]["mensagens"]) == 4
        await service.start(first.session_id, user)
        await service.chat(ChatRequest(message="Após reabrir", session_id=first.session_id), user)
        snapshot = await service.graph.aget_state(service.checkpoints.config(user.uid, sid))
        assert len(snapshot.values["messages"]) == 6
        await service.close()
        assert not (await service.graph.aget_state(service.checkpoints.config(user.uid, sid))).values

    run(scenario)


def test_local_session_limit_evicts_old_checkpoints_without_deleting_history():
    async def scenario():
        service, _, repository, user = service_parts()
        service.checkpoints.max_threads = 20
        sessions = []
        for _ in range(21):
            sessions.append(await service.chat(ChatRequest(message="Olá"), user))
        oldest = service.checkpoints.config(user.uid, str(sessions[0].session_id))
        newest = service.checkpoints.config(user.uid, str(sessions[-1].session_id))
        assert not (await service.graph.aget_state(oldest)).values
        assert (await service.graph.aget_state(newest)).values
        assert len(repository.docs) == 21
        assert len(list(service.checkpoints.saver.list(None))) == 20

    run(scenario)


def test_timeout_discards_incomplete_checkpoint_and_releases_session():
    async def scenario():
        service, model, repository, user = service_parts()
        first = await service.chat(ChatRequest(message="Olá"), user)
        service.request_timeout = 0.02

        async def slow(*args, **kwargs):
            await asyncio.sleep(1)

        model.complete = slow
        with pytest.raises(ChatError) as error:
            await service.chat(ChatRequest(message="Explique RH", session_id=first.session_id), user)
        assert error.value.status_code == 504
        config = service.checkpoints.config(user.uid, str(first.session_id))
        assert not (await service.graph.aget_state(config)).values
        assert len(repository.docs[str(first.session_id)]["mensagens"]) == 2
        assert "lock_token" not in repository.docs[str(first.session_id)]
        assert not service.checkpoints._active

    run(scenario)
