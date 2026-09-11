import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from qdrant_client import AsyncQdrantClient, models

from app.core import config
from app.infrastructure.llm.embeddings import gerar_embeddings_batch
from app.modules.chat.errors import ChatError
from app.scripts import ingest_faq


@pytest.fixture
def pdfs(tmp_path, monkeypatch):
    files = [tmp_path / "normas.pdf", tmp_path / "beneficios.PDF"]
    for path in files:
        path.touch()
    def reader(stream):
        return SimpleNamespace(is_encrypted=False, pages=[
            SimpleNamespace(extract_text=lambda: ("Orientacao de FAQ. " * 2500)),
            SimpleNamespace(extract_text=lambda: "Segunda pagina do FAQ."),
        ])
    monkeypatch.setattr(ingest_faq, "PdfReader", reader)
    return files


async def embed(texts):
    return [[1.0] + [0.0] * 1023 for _ in texts]


@pytest.mark.parametrize("vector_name", [None, "", "default"])
def test_replace_all_points_and_preserve_other_collection(pdfs, vector_name):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        params = models.VectorParams(size=1024, distance=models.Distance.COSINE)
        await client.create_collection("faq_chunks", vectors_config=(
            params if vector_name is None else {vector_name: params}))
        await client.create_collection("memoria_resumos", vectors_config=params)
        old_id = str(uuid4())
        vector = [1.0] + [0.0] * 1023
        await client.upsert("faq_chunks", points=[models.PointStruct(id=old_id,
            vector=vector if vector_name is None else {vector_name: vector}, payload={"old": True})])
        await client.upsert("memoria_resumos", points=[models.PointStruct(id=old_id, vector=vector)])
        batches = AsyncMock(side_effect=embed)
        first = await ingest_faq.ingerir_faq([pdfs[0].parent, pdfs[0]], client=client, embed_batch=batches)
        assert first > 50
        assert all(len(call.args[0]) <= 50 for call in batches.await_args_list)
        points, _ = await client.scroll("faq_chunks", limit=1000)
        assert len(points) == first and old_id not in {str(p.id) for p in points}
        assert {p.payload["source"] for p in points} == {p.name for p in pdfs}
        assert {p.payload["page_number"] for p in points} == {0, 1}
        assert all(len(p.payload["page_content"]) <= 700 for p in points)
        assert all(p.payload["modelo_embedding"] == "mistral-embed" for p in points)
        second = await ingest_faq.ingerir_faq([pdfs[0]], client=client, embed_batch=embed)
        assert second < first and (await client.count("faq_chunks")).count == second
        points, _ = await client.scroll("faq_chunks", limit=1000)
        assert {p.payload["source"] for p in points} == {pdfs[0].name}
        assert (await client.count("memoria_resumos")).count == 1
        await client.close()
    asyncio.run(scenario())


def test_embedding_failure_keeps_existing_points(pdfs):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        await client.create_collection("faq_chunks", vectors_config=models.VectorParams(
            size=1024, distance=models.Distance.COSINE))
        old_id = str(uuid4())
        await client.upsert("faq_chunks", points=[models.PointStruct(
            id=old_id, vector=[1.0] * 1024)])
        calls = 0
        async def fail_second(texts):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ChatError(503, "Falha simulada")
            return await embed(texts)
        with pytest.raises(RuntimeError, match="Nenhum ponto existente foi apagado"):
            await ingest_faq.ingerir_faq(pdfs, client=client, embed_batch=fail_second)
        assert (await client.count("faq_chunks")).count == 1
        assert len(await client.retrieve("faq_chunks", [old_id])) == 1
        await client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("size,distance", [(768, models.Distance.COSINE), (1024, models.Distance.DOT)])
def test_collection_mismatch_before_embedding_or_deletion(pdfs, size, distance):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        await client.create_collection("faq_chunks", vectors_config=models.VectorParams(size=size, distance=distance))
        client.delete = AsyncMock()
        embeddings = AsyncMock()
        with pytest.raises(RuntimeError, match="1024 dimensoes e Cosine"):
            await ingest_faq.ingerir_faq(pdfs, client=client, embed_batch=embeddings)
        client.delete.assert_not_awaited()
        embeddings.assert_not_awaited()
        await client.close()
    asyncio.run(scenario())


def test_invalid_or_empty_input_never_touches_qdrant(pdfs, monkeypatch, tmp_path):
    async def scenario():
        client = AsyncMock()
        for paths in ([], [tmp_path / "missing.pdf"]):
            with pytest.raises(ValueError):
                await ingest_faq.ingerir_faq(paths, client=client)
        monkeypatch.setattr(ingest_faq, "PdfReader", lambda stream: SimpleNamespace(
            is_encrypted=False, pages=[SimpleNamespace(extract_text=lambda: "")]))
        with pytest.raises(ValueError, match="OCR"):
            await ingest_faq.ingerir_faq(pdfs, client=client)
        client.get_collection.assert_not_awaited()
        client.delete.assert_not_awaited()
    asyncio.run(scenario())


def test_upsert_failure_reports_partial_state(pdfs):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        await client.create_collection("faq_chunks", vectors_config=models.VectorParams(
            size=1024, distance=models.Distance.COSINE))
        client.upsert = AsyncMock(side_effect=RuntimeError("private credentials"))
        with pytest.raises(RuntimeError, match="vazia ou parcial") as error:
            await ingest_faq.ingerir_faq(pdfs, client=client, embed_batch=embed)
        assert "private credentials" not in str(error.value)
        await client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", [False, True])
def test_batch_embeddings_order_and_cardinality(monkeypatch, invalid):
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "fake-key")
    original = httpx.AsyncClient
    def handler(request):
        assert json.loads(request.content)["input"] == ["a", "b"]
        data = [{"index": 1, "embedding": [2.0] * 1024}, {"index": 0, "embedding": [1.0] * 1024}]
        return httpx.Response(200, json={"data": data[:1] if invalid else data})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs))
    if invalid:
        with pytest.raises(ChatError):
            asyncio.run(gerar_embeddings_batch(["a", "b"]))
    else:
        result = asyncio.run(gerar_embeddings_batch(["a", "b"]))
        assert result[0][0] == 1.0 and result[1][0] == 2.0
