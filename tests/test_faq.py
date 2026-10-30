import asyncio
from uuid import uuid4

import pytest
from qdrant_client import AsyncQdrantClient, models

from app.core import config
from app.infrastructure.vectorstore import faq
from app.infrastructure.vectorstore.faq import FaqVectors
from app.modules.chat.errors import ChatError


QUERY_VECTOR = [1.0] + [0.0] * 1535
OTHER_VECTOR = [0.0, 1.0] + [0.0] * 1534


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
        params = models.VectorParams(size=1536, distance=models.Distance.COSINE)
        await store.client.create_collection("faq_chunks_openai_te3s_v1", vectors_config=(
            params if vector_name is None else {vector_name: params}
        ))
        def vector(value):
            return value if vector_name is None else {vector_name: value}
        await store.client.upsert("faq_chunks_openai_te3s_v1", points=[
            models.PointStruct(id=str(uuid4()), vector=vector(QUERY_VECTOR), payload={
                "modelo_embedding": "openai/text-embedding-3-small", "page_content": "O objetivo do Astro é organizar as informações.",
                "page_number": 0,
                "source": source,
            }),
            models.PointStruct(id=str(uuid4()), vector=vector(OTHER_VECTOR), payload={
                "modelo_embedding": "openai/text-embedding-3-small", "page_content": "Trecho sem relação.", "page_number": 1, "source": "outro.pdf",
            }),
            models.PointStruct(id=str(uuid4()), vector=vector(QUERY_VECTOR), payload={
                "modelo_embedding": "openai/text-embedding-3-small", "page_content": "", "page_number": 2, "source": "invalido.pdf",
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
        assert (await store.client.count("faq_chunks_openai_te3s_v1")).count == 3
        await store.close()
    asyncio.run(scenario())


def test_faq_rejects_incompatible_collection_without_changing_it(monkeypatch):
    async def scenario():
        store = FaqVectors()
        store.client = AsyncQdrantClient(":memory:")
        await store.client.create_collection("faq_chunks_openai_te3s_v1", vectors_config=models.VectorParams(
            size=768, distance=models.Distance.COSINE,
        ))
        monkeypatch.setattr(config, "QDRANT_URL", "http://localhost:6333")
        with pytest.raises(ChatError) as error:
            await store.search("Pergunta")
        assert error.value.status_code == 503
        assert "1536/Cosine" in error.value.detail
        assert (await store.client.count("faq_chunks_openai_te3s_v1")).count == 0
        await store.close()
    asyncio.run(scenario())

def test_faq_does_not_mix_models_even_with_equal_dimension(monkeypatch):
    from unittest.mock import AsyncMock
    async def scenario():
        store = FaqVectors()
        store.client = AsyncQdrantClient(":memory:")
        await store.client.create_collection(faq.COLLECTION_FAQ,
            vectors_config=models.VectorParams(size=1536, distance=models.Distance.COSINE))
        await store.client.upsert(faq.COLLECTION_FAQ, points=[
            models.PointStruct(id=str(uuid4()), vector=QUERY_VECTOR, payload={
                "page_content": "Legado indevido", "source": "legado.pdf", "page_number": 0,
                "modelo_embedding": "mistral-embed"}),
            models.PointStruct(id=str(uuid4()), vector=QUERY_VECTOR, payload={
                "page_content": "Texto validado", "source": "novo.pdf", "page_number": 0,
                "modelo_embedding": "openai/text-embedding-3-small"}),
        ])
        monkeypatch.setattr(config, "QDRANT_URL", "http://localhost:6333")
        monkeypatch.setattr(faq, "gerar_embeddings_batch", AsyncMock(return_value=[QUERY_VECTOR]))
        assert [s["fonte"] for s in await store.search("pergunta")] == ["novo.pdf"]
        await store.close()
    asyncio.run(scenario())


def test_faq_preserves_evidence_ranked_sixth_and_rejects_irrelevant_results(monkeypatch):
    """Regressão da calibração real: público-alvo ficou em sexto, score 0,5149."""
    import math
    from unittest.mock import AsyncMock

    async def scenario():
        store = FaqVectors()
        store.client = AsyncQdrantClient(":memory:")
        await store.client.create_collection(faq.COLLECTION_FAQ, vectors_config=models.VectorParams(
            size=1536, distance=models.Distance.COSINE))
        points = []
        for rank, score in enumerate((0.6634, 0.6382, 0.6083, 0.606, 0.5777, 0.5149, 0.2006)):
            vector = [score, math.sqrt(1 - score * score)] + [0.0] * 1534
            points.append(models.PointStruct(id=str(uuid4()), vector=vector, payload={
                "modelo_embedding": "openai/text-embedding-3-small",
                "page_content": "PÚBLICO-ALVO: RH, empresas e colaboradores." if rank == 5 else "Outro trecho.",
                "source": "uso_astro.pdf", "page_number": rank,
            }))
        await store.client.upsert(faq.COLLECTION_FAQ, points=points)
        embed = AsyncMock(return_value=[QUERY_VECTOR])
        monkeypatch.setattr(config, "QDRANT_URL", "http://localhost:6333")
        monkeypatch.setattr(faq, "gerar_embeddings_batch", embed)
        monkeypatch.setattr(faq, "FAQ_SCORE_THRESHOLD", 0.35)
        snippets = await store.search("Para quem a plataforma Astro é destinada?")
        assert len(snippets) == 6
        assert snippets[-1]["pagina"] == 6
        assert snippets[-1]["conteudo"].startswith("PÚBLICO-ALVO")
        assert all(item["relevancia"] >= 0.35 for item in snippets)
        embed.assert_awaited_once()
        assert (await store.client.count(faq.COLLECTION_FAQ)).count == 7
        await store.close()
    asyncio.run(scenario())
