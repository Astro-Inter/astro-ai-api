"""Contrato entre a API e o worker de resumos (SCRUM-474), sem serviços reais."""

import asyncio
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.core.security import CurrentUser
from app.infrastructure.database.sessions import MongoSessions, utc_now
from app.modules.chat.schemas import ChatRequest
from app.modules.chat.service import ChatService
from memory_fakes import FakeFaqVectors, FakeSessions, FakeVectors
from test_chat import FakeModel


def test_only_messages_change_last_interaction_and_invalidate_index():
    async def scenario():
        repo = MongoSessions()
        update_one = AsyncMock(return_value=SimpleNamespace(matched_count=1))
        repo.collection, repo.ready = SimpleNamespace(update_one=update_one), True
        await repo.update("sid", "owner", "token", {"titulo": "Título"})
        fields = update_one.call_args.args[1]["$set"]
        assert "ultima_mensagem_em" not in fields
        assert "resumo_indexado" not in fields
        await repo.update("sid", "owner", "token", {}, messages=[
            {"role": "human", "content": "Oi"}, {"role": "assistant", "content": "Olá"},
        ])
        update = update_one.call_args.args[1]
        assert update["$set"]["ultima_mensagem_em"] == update["$set"]["atualizada_em"]
        assert update["$set"]["resumo_indexado"] is False
        assert len(update["$push"]["mensagens"]["$each"]) == 2
    asyncio.run(scenario())


def test_active_automatic_summary_is_searchable_and_conversation_continues():
    async def scenario():
        repo, vectors, model = FakeSessions(), FakeVectors(), FakeModel("direta")
        service = ChatService(model, repository=repo, vectors=vectors, faq_vectors=FakeFaqVectors())
        owner = CurrentUser(uid="owner", role="COLABORADOR")
        first = await service.chat(ChatRequest(message="Oi"), owner)
        sid = str(first.session_id)
        doc = repo.docs[sid]
        doc.update(resumo="Falamos sobre RH", resumo_mensagens=2, resumo_indexado=False, resumo_origem="automatico",
                   resumo_ultima_mensagem_em=doc["ultima_mensagem_em"])
        # Mesmo sem embedding, o resumo do Mongo é consultável pelo seu dono.
        result = await service.memory.search("owner", "other-session", "RH")
        assert result["conversas"][0]["session_id"] == sid
        assert result["origem"] == "busca_semantica_sem_resultados_resumos_nao_indexados_mongodb"
        assert not (await repo.previous("other-owner", "other-session"))
        assert not (await repo.previous("owner", sid))
        await service.chat(ChatRequest(message="Outra pergunta", session_id=first.session_id), owner)
        assert doc["status"] == "ativa"
        assert doc["resumo"] == "Falamos sobre RH"
        assert not await repo.previous("owner", "other-session")
    asyncio.run(scenario())


def test_internal_updates_do_not_hide_fresh_summary():
    async def scenario():
        repo = FakeSessions()
        sid = str(uuid4())
        await repo.ensure(sid, "owner")
        last = utc_now() - timedelta(days=2)
        repo.docs[sid].update(ultima_mensagem_em=last, resumo="Memória", resumo_mensagens=0,
                              resumo_ultima_mensagem_em=last)
        doc, token = await repo.acquire(sid, "owner")
        await repo.update(sid, "owner", token, {"titulo": "Atualização interna"})
        assert len(await repo.previous("owner", "other-session")) == 1
        assert repo.docs[sid]["ultima_mensagem_em"] == last
    asyncio.run(scenario())


def test_manual_legacy_end_does_not_reuse_stale_automatic_summary():
    async def scenario():
        repo, vectors, model = FakeSessions(), FakeVectors(), FakeModel("direta")
        model.replies["resumo"] = '{"resumo":"Resumo novo"}'
        service = ChatService(model, repository=repo, vectors=vectors, faq_vectors=FakeFaqVectors())
        owner = CurrentUser(uid="owner", role="COLABORADOR")
        result = await service.chat(ChatRequest(message="Oi"), owner)
        doc = repo.docs[str(result.session_id)]
        doc.update(resumo="Obsoleto", resumo_mensagens=1,
                   resumo_ultima_mensagem_em=utc_now() - timedelta(days=1))
        assert (await service.end(result.session_id, owner)).resumo == "Resumo novo"
        assert doc["resumo_mensagens"] == len(doc["mensagens"])
    asyncio.run(scenario())
