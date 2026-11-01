import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from qdrant_client import AsyncQdrantClient, models

from app.scripts import migrate_faq


async def source(client, payload=None):
    await client.create_collection(
        "faq_chunks",
        vectors_config=models.VectorParams(size=1024, distance=models.Distance.COSINE),
    )
    await client.upsert(
        "faq_chunks",
        points=[
            models.PointStruct(
                id=str(uuid4()),
                vector=[1.0] * 1024,
                payload=payload
                if payload is not None
                else {
                    "page_content": "Texto original FAQ.",
                    "source": "astro.pdf",
                    "page_number": 0,
                    "modelo_embedding": "mistral-embed",
                },
            )
        ],
    )


def args(**changes):
    return SimpleNamespace(
        **{
            "source": "faq_chunks",
            "target": "faq_chunks_openai_te3s_20261101",
            "apply": False,
            "expected_points": None,
            "source_fingerprint": None,
            "max_cost_usd": None,
            **changes,
        }
    )


def test_faq_dry_run_needs_no_mongo_or_paid_calls_and_preserves_legacy():
    async def run():
        client = AsyncQdrantClient(":memory:")
        await source(client)
        embed = AsyncMock()
        report = await migrate_faq.migrate(args(), client, embed)
        assert (
            report["points"] == 1
            and report["dimensions"] == 1024
            and not report["applied"]
        )
        assert not await client.collection_exists(args().target)
        embed.assert_not_awaited()
        assert (
            await client.get_collection("faq_chunks")
        ).config.params.vectors.size == 1024
        await client.close()

    asyncio.run(run())


def test_faq_apply_reembeds_text_and_validates_new_collection_without_changing_source():
    async def run():
        client = AsyncQdrantClient(":memory:")
        await source(client)
        before, _ = await client.scroll("faq_chunks", with_vectors=True)
        plan = await migrate_faq.migrate(args(), client)
        embed = AsyncMock(return_value=[[1.0] + [0.0] * 1023])
        result = await migrate_faq.migrate(
            args(
                apply=True,
                expected_points=1,
                source_fingerprint=plan["source_fingerprint"],
                max_cost_usd=1,
            ),
            client,
            embed,
        )
        assert result["applied"]
        embed.assert_awaited_once_with(["Texto original FAQ."])
        after, _ = await client.scroll("faq_chunks", with_vectors=True)
        assert after == before
        new, _ = await client.scroll(args().target, with_vectors=True)
        assert len(new[0].vector) == 1024 and new[0].id == before[0].id
        assert new[0].payload["modelo_embedding"] == "openai/text-embedding-3-small"
        with pytest.raises(ValueError):
            await migrate_faq.migrate(args(), client, embed)
        await client.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes",
    [
        {"expected_points": 2},
        {"source_fingerprint": "changed"},
        {"max_cost_usd": 0},
        {"max_cost_usd": float("nan")},
    ],
)
def test_apply_requires_reviewed_volume_cost_and_same_source(changes):
    async def run():
        client = AsyncQdrantClient(":memory:")
        await source(client)
        plan = await migrate_faq.migrate(args(), client)
        values = {
            "apply": True,
            "expected_points": 1,
            "source_fingerprint": plan["source_fingerprint"],
            "max_cost_usd": 1,
            **changes,
        }
        embed = AsyncMock()
        with pytest.raises(ValueError):
            await migrate_faq.migrate(args(**values), client, embed)
        embed.assert_not_awaited()
        assert not await client.collection_exists(args().target)
        await client.close()

    asyncio.run(run())


def test_faq_incomplete_source_refuses_migration():
    async def run():
        client = AsyncQdrantClient(":memory:")
        await source(client, {"page_content": "Texto sem fonte."})
        with pytest.raises(ValueError):
            await migrate_faq.migrate(args(), client)
        assert not await client.collection_exists(args().target)
        await client.close()

    asyncio.run(run())
