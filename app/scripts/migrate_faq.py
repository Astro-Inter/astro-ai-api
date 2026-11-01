"""Migra somente textos FAQ do Qdrant; sem --apply apenas inventaria e estima."""

import argparse
import asyncio
import hashlib
import json
import math

from qdrant_client import AsyncQdrantClient, models

from app.core import config
from app.infrastructure.llm.embeddings import (
    FAQ_EMBEDDING_DIMENSIONS as EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    gerar_embeddings_faq as gerar_embeddings_batch,
)
from app.scripts.migrate_vectors import estimate, populate


async def collect(client, source):
    records, offset = [], None
    while True:
        batch, offset = await client.scroll(
            source, limit=100, offset=offset, with_payload=True, with_vectors=False
        )
        for point in batch:
            payload = point.payload or {}
            text = payload.get("page_content")
            if (
                not isinstance(text, str)
                or not text.strip()
                or not isinstance(payload.get("source"), str)
                or not payload["source"].strip()
                or type(payload.get("page_number")) is not int
                or payload["page_number"] < 0
            ):
                raise ValueError("Fonte FAQ incompleta; nenhuma migracao autorizada.")
            records.append(
                (point.id, text, {**payload, "modelo_embedding": EMBEDDING_MODEL})
            )
        if len(records) > 10000:
            raise ValueError(
                "Inventario excede 10000 pontos; revise o plano antes de continuar."
            )
        if offset is None:
            break
    if not records or len({str(r[0]) for r in records}) != len(records):
        raise ValueError("Fonte vazia ou IDs duplicados.")
    return records


def fingerprint(records):
    value = sorted((str(pid), payload) for pid, _, payload in records)
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
    ).hexdigest()


async def migrate(args, client, embed=gerar_embeddings_batch):
    if args.source == args.target or "_openai_te3s_" not in args.target:
        raise ValueError("Destino deve ser separado e conter _openai_te3s_.")
    if await client.collection_exists(args.target):
        raise ValueError("Destino existente nao pode ser reaproveitado ou sobrescrito.")
    records = await collect(client, args.source)
    digest = fingerprint(records)
    report = {
        "source": args.source,
        "target": args.target,
        **estimate(records),
        "source_fingerprint": digest,
        "model": EMBEDDING_MODEL,
        "dimensions": EMBEDDING_DIMENSIONS,
        "applied": False,
    }
    print(json.dumps(report))
    if not args.apply:
        return report
    if (
        args.expected_points != len(records)
        or args.source_fingerprint != digest
        or args.max_cost_usd is None
        or not math.isfinite(args.max_cost_usd)
        or args.max_cost_usd < report["cost_usd_upper_bound"]
    ):
        raise ValueError("Revise volume, fingerprint e teto de custo antes de aplicar.")
    # Refuse source drift between planning and the creation of a destination.
    if fingerprint(await collect(client, args.source)) != digest:
        raise ValueError("Fonte mudou durante o inventario.")
    await client.create_collection(
        args.target,
        vectors_config=models.VectorParams(
            size=EMBEDDING_DIMENSIONS, distance=models.Distance.COSINE
        ),
    )
    await client.create_payload_index(
        args.target, "modelo_embedding", models.PayloadSchemaType.KEYWORD, wait=True
    )
    await populate(client, args.target, records, embed)
    if fingerprint(await collect(client, args.source)) != digest:
        raise ValueError("Fonte mudou durante a migracao; nao ative o destino.")
    report["applied"] = True
    print(
        json.dumps(
            {
                "validated_target": args.target,
                "points": len(records),
                "activation": "manual; calibre e publique somente apos autorizacao",
            }
        )
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="faq_chunks")
    parser.add_argument("--target", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-points", type=int)
    parser.add_argument("--source-fingerprint")
    parser.add_argument("--max-cost-usd", type=float)
    args = parser.parse_args()

    async def run():
        if not config.QDRANT_URL:
            raise ValueError("Configure QDRANT_URL no backend.")
        client = AsyncQdrantClient(
            url=config.QDRANT_URL,
            api_key=config.QDRANT_API_KEY,
            timeout=30,
            check_compatibility=False,
        )
        try:
            await migrate(args, client)
        finally:
            await client.close()

    try:
        asyncio.run(run())
    except Exception as error:
        parser.exit(
            1,
            "Migracao FAQ interrompida ("
            + type(error).__name__
            + "). Fonte preservada; nao ative destinos parciais.\n",
        )


if __name__ == "__main__":
    main()
