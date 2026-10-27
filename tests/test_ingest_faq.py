import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from qdrant_client import AsyncQdrantClient, models

from app.core import config
from app.infrastructure.llm.embeddings import gerar_embeddings_batch, EMBEDDING_MODEL
from app.modules.chat.errors import ChatError
from app.scripts import ingest_faq, migrate_vectors


@pytest.fixture
def pdfs(tmp_path, monkeypatch):
    files = [tmp_path / "normas.pdf", tmp_path / "beneficios.PDF"]
    for path in files:
        path.touch()
    monkeypatch.setattr(ingest_faq, "PdfReader", lambda stream: SimpleNamespace(
        is_encrypted=False, pages=[SimpleNamespace(extract_text=lambda: "Orientacao de FAQ. " * 2500),
                                   SimpleNamespace(extract_text=lambda: "Segunda pagina do FAQ.")]))
    return files


async def embed(texts):
    return [[1.0] + [0.0] * 1535 for _ in texts]


def test_new_collection_preserves_legacy_and_refuses_overwrite(pdfs):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        await client.create_collection("faq_chunks", vectors_config=models.VectorParams(size=1024, distance=models.Distance.COSINE))
        pid = str(uuid4())
        await client.upsert("faq_chunks", points=[models.PointStruct(id=pid, vector=[1.0] * 1024)])
        batches = AsyncMock(side_effect=embed)
        total = await ingest_faq.ingerir_faq(pdfs, client=client, embed_batch=batches)
        assert total > 50
        assert all(len(c.args[0]) <= 50 for c in batches.await_args_list)
        points, _ = await client.scroll(ingest_faq.COLLECTION_FAQ, limit=1000)
        assert len(points) == total
        assert all(p.payload["modelo_embedding"] == EMBEDDING_MODEL for p in points)
        assert {p.payload["source"] for p in points} == {p.name for p in pdfs}
        assert (await client.count("faq_chunks")).count == 1
        with pytest.raises(RuntimeError, match="Fontes preservadas"):
            await ingest_faq.ingerir_faq(pdfs, client=client, embed_batch=batches)
        assert (await client.count(ingest_faq.COLLECTION_FAQ)).count == total
        await client.close()
    asyncio.run(scenario())


def test_failure_leaves_partial_target_and_preserves_source(pdfs):
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        client.upsert = AsyncMock(side_effect=RuntimeError("private-key"))
        with pytest.raises(RuntimeError, match="parcial") as error:
            await ingest_faq.ingerir_faq(pdfs, client=client, embed_batch=embed)
        assert "private-key" not in str(error.value)
        await client.close()
    asyncio.run(scenario())


def test_invalid_empty_input_never_touches_qdrant(pdfs, monkeypatch, tmp_path):
    async def scenario():
        client = AsyncMock()
        for paths in ([], [tmp_path / "missing.pdf"]):
            with pytest.raises(ValueError):
                await ingest_faq.ingerir_faq(paths, client=client)
        monkeypatch.setattr(ingest_faq, "PdfReader", lambda stream: SimpleNamespace(
            is_encrypted=False, pages=[SimpleNamespace(extract_text=lambda: "")]))
        with pytest.raises(ValueError, match="OCR"):
            await ingest_faq.ingerir_faq(pdfs, client=client)
        client.create_collection.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", [False, True])
def test_batch_embeddings_order_cardinality_dimensions_and_redaction(monkeypatch, invalid):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "fake-key")
    original = httpx.AsyncClient
    def handler(request):
        body = json.loads(request.content)
        assert body["input"] == ["a", "b"] and body["dimensions"] == 1536
        assert body["model"] == EMBEDDING_MODEL
        data = [{"index": 1, "embedding": [2.0] * 1536}, {"index": 0, "embedding": [1.0] * 1536}]
        return httpx.Response(200, json={"data": data[:1] if invalid else data})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    if invalid:
        with pytest.raises(ChatError):
            asyncio.run(gerar_embeddings_batch(["a", "b"]))
    else:
        result = asyncio.run(gerar_embeddings_batch(["a", "b"]))
        assert result[0][0] == 1 and result[1][0] == 2


@pytest.mark.parametrize("vector", [[1.0] * 1024, [0.0] * 1536, [float("nan")] * 1536, [True] * 1536])
def test_invalid_vector_rejected(monkeypatch, vector):
    monkeypatch.setattr("app.infrastructure.llm.embeddings.request", AsyncMock(return_value={"data": [{"index": 0, "embedding": vector}]}))
    with pytest.raises(ChatError):
        asyncio.run(gerar_embeddings_batch(["teste"]))


def test_migration_new_targets_validate_ids_payload_and_tenant_filter():
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        targets = {"faq": "faq_openai_te3s_test", "memory": "memory_openai_te3s_test"}
        await migrate_vectors.create_targets(client, targets)
        rows = [(str(uuid4()), "Resumo", {"id_user": owner, "modelo_embedding": EMBEDDING_MODEL}) for owner in ("a", "b")]
        await migrate_vectors.populate(client, targets["memory"], rows, embed)
        matches = await client.query_points(targets["memory"], query=(await embed(["x"]))[0],
            query_filter=models.Filter(must=[models.FieldCondition(key="id_user", match=models.MatchValue(value="a"))]))
        assert len(matches.points) == 1 and str(matches.points[0].id) == rows[0][0]
        with pytest.raises(ValueError):
            await migrate_vectors.create_targets(client, targets)
        await client.close()
    asyncio.run(scenario())


def test_estimate_has_no_paid_call_and_accounts_for_utf8():
    report = migrate_vectors.estimate([(1, "ação", {})])
    assert report["input_tokens_upper_bound"] == 6 and report["points"] == 1

def test_read_sources_uses_canonical_summaries_and_reports_missing_text():
    from datetime import datetime, timezone
    class Sessions:
        def find(self, *args):
            async def rows():
                yield {"_id": str(uuid4()), "id_user": "owner", "resumo": "Resumo canônico", "iniciada_em": datetime.now(timezone.utc)}
                yield {"_id": "invalid", "resumo": "Resumo sem dono"}
            return rows()
    async def scenario():
        client = AsyncQdrantClient(":memory:")
        await client.create_collection("faq_chunks", vectors_config=models.VectorParams(size=1024, distance=models.Distance.COSINE))
        await client.upsert("faq_chunks", points=[models.PointStruct(id=str(uuid4()), vector=[1.0] * 1024, payload={})])
        records, missing = await migrate_vectors.collect_sources(client, Sessions())
        assert missing == {"faq": 1, "memory": 1}
        assert len(records["memory"]) == 1
        assert records["memory"][0][2]["id_user"] == "owner"
        assert records["memory"][0][2]["modelo_embedding"] == EMBEDDING_MODEL
        await client.close()
    asyncio.run(scenario())


def test_targets_validate_all_names_before_any_creation():
    client = AsyncMock()
    client.collection_exists = AsyncMock(return_value=False)
    with pytest.raises(ValueError):
        asyncio.run(migrate_vectors.create_targets(client, {"faq": "faq_openai_te3s_test", "memory": "memoria_resumos"}))
    client.create_collection.assert_not_awaited()


def test_calibration_counts_false_results_without_ai():
    from app.scripts.calibrate_faq import metrics
    rows = [({"normas.pdf"}, [{"fonte": "normas.pdf", "relevancia": 0.6}]),
            (set(), [{"fonte": "outro.pdf", "relevancia": 0.3}])]
    assert metrics(rows, 0.35) == {"threshold": 0.35, "precision": 1.0, "recall": 1.0,
                                  "false_positives": 0, "false_negatives": 0}
    assert metrics(rows, 0.25)["false_positives"] == 1
