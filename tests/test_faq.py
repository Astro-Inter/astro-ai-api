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


@pytest.mark.parametrize("source,expected", [
    (r"C:\documentos\norma.pdf", "norma.pdf"),
    ("/home/runner/documentos/norma.pdf", "norma.pdf"),
    (r"\\servidor\compartilhamento\norma.pdf", "norma.pdf"),
    (r"C:/documentos\norma.pdf", "norma.pdf"),
    (r"documentos\norma.pdf", "norma.pdf"),
    ("documentos/norma.pdf", "norma.pdf"),
    ("norma.pdf", "norma.pdf"),
    ("  /documentos/norma.pdf  ", "norma.pdf"),
    (None, "Documento FAQ"),
    (123, "Documento FAQ"),
    ("", "Documento FAQ"),
    ("   ", "Documento FAQ"),
    ("/", "Documento FAQ"),
    ("C:\\", "Documento FAQ"),
    (r"\\servidor\compartilhamento", "Documento FAQ"),
])
def test_faq_source_sanitization_is_platform_independent(source, expected):
    assert faq._fonte_publica(source) == expected


@pytest.mark.parametrize("vector_name", [None, "", "default"])
@pytest.mark.parametrize("source", [
    r"C:\documentos\Astro_Instrucao_Normativa_v1.0.pdf",
    "/home/runner/documentos/Astro_Instrucao_Normativa_v1.0.pdf",
    r"\\servidor\documentos\Astro_Instrucao_Normativa_v1.0.pdf",
    r"C:/documentos\Astro_Instrucao_Normativa_v1.0.pdf",
])
def test_faq_search_returns_relevant_sanitized_chunks(monkeypatch, vector_name, source):
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
                "source": source,
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
