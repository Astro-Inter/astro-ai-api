import asyncio
from uuid import uuid4

import pytest
from qdrant_client import AsyncQdrantClient, models

from app.core import config
from app.infrastructure.vectorstore import faq
from app.infrastructure.vectorstore.faq import FaqVectors
from app.modules.chat.errors import ChatError


QUERY_VECTOR = [1.0] + [0.0] * 1023
OTHER_VECTOR = [0.0, 1.0] + [0.0] * 1022


@pytest.mark.parametrize("vector_name", [None, "", "default"])
def test_faq_search_returns_relevant_sanitized_chunks(monkeypatch, vector_name):
    async def scenario():
        store = FaqVectors()
        store.client = AsyncQdrantClient(":memory:")
        params = models.VectorParams(size=1024, distance=models.Distance.COSINE)
        await store.client.create_collection("faq_chunks", vectors_config=(
            params if vector_name is None else {vector_name: params}
        ))
        def vector(value):
            return value if vector_name is None else {vector_name: value}
        await store.client.upsert("faq_chunks", points=[
            models.PointStruct(id=str(uuid4()), vector=vector(QUERY_VECTOR), payload={
                "page_content": "O objetivo do Astro é organizar as informações.",
                "page_number": 0,
                "source": r"C:\documentos\Astro_Instrucao_Normativa_v1.0.pdf",
            }),
            models.PointStruct(id=str(uuid4()), vector=vector(OTHER_VECTOR), payload={
                "page_content": "Trecho sem relação.", "page_number": 1, "source": "outro.pdf",
            }),
            models.PointStruct(id=str(uuid4()), vector=vector(QUERY_VECTOR), payload={
                "page_content": "", "page_number": 2, "source": "invalido.pdf",
            }),
        ])
        async def embed(texts):
            assert texts == ["Qual é o objetivo do Astro?"]
            return [QUERY_VECTOR]
        monkeypatch.setattr(faq, "gerar_embeddings_batch", embed)
        monkeypatch.setattr(config, "QDRANT_URL", "http://localhost:6333")
        snippets = await store.search("Qual é o objetivo do Astro?")
        assert snippets == [{
            "conteudo": "O objetivo do Astro é organizar as informações.",
            "fonte": "Astro_Instrucao_Normativa_v1.0.pdf",
            "pagina": 1,
            "relevancia": 1.0,
        }]
        assert (await store.client.count("faq_chunks")).count == 3
        await store.close()
    asyncio.run(scenario())


def test_faq_rejects_incompatible_collection_without_changing_it(monkeypatch):
    async def scenario():
        store = FaqVectors()
        store.client = AsyncQdrantClient(":memory:")
        await store.client.create_collection("faq_chunks", vectors_config=models.VectorParams(
            size=768, distance=models.Distance.COSINE,
        ))
        monkeypatch.setattr(config, "QDRANT_URL", "http://localhost:6333")
        with pytest.raises(ChatError) as error:
            await store.search("Pergunta")
        assert error.value.status_code == 503
        assert "1024/Cosine" in error.value.detail
        assert (await store.client.count("faq_chunks")).count == 0
        await store.close()
    asyncio.run(scenario())
