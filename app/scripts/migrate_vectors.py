"""Migração conservadora de vetores. Sem --apply apenas lê e estima; nunca apaga."""
import argparse
import asyncio
import json
import math
from uuid import UUID

from pymongo import AsyncMongoClient
from qdrant_client import AsyncQdrantClient, models

from app.core import config
from app.core.pii import redact_for_embedding
from app.infrastructure.llm.embeddings import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, gerar_embeddings_batch

PRICE_PER_TOKEN_USD = 0.00000002  # Catálogo OpenRouter, 26/10/2026; reconfirmar antes de aplicar.


async def read_points(client, collection):
    points, offset = [], None
    while True:
        batch, offset = await client.scroll(collection, limit=100, offset=offset,
                                            with_payload=True, with_vectors=False)
        points.extend(batch)
        if offset is None:
            return points


def estimate(records):
    # Bytes UTF-8 são um teto conservador para tokens BPE; inclui pseudonimização.
    upper = sum(len(redact_for_embedding(text).encode("utf-8")) for _, text, _ in records)
    return {"points": len(records), "input_tokens_upper_bound": upper,
            "cost_usd_upper_bound": round(upper * PRICE_PER_TOKEN_USD, 8)}


async def collect_sources(client, sessions, faq_source="faq_chunks"):
    records, missing = {"faq": [], "memory": []}, {"faq": 0, "memory": 0}
    for point in await read_points(client, faq_source):
        payload = point.payload or {}
        text = payload.get("page_content")
        if (not isinstance(text, str) or not text.strip() or not isinstance(payload.get("source"), str)
                or type(payload.get("page_number")) is not int):
            missing["faq"] += 1
            continue
        records["faq"].append((point.id, text, {**payload, "modelo_embedding": EMBEDDING_MODEL}))
    # MongoDB é a fonte canônica: inclui resumos ainda não presentes no índice legado.
    async for doc in sessions.find({"resumo": {"$type": "string", "$ne": ""}},
                                   {"_id": 1, "id_user": 1, "resumo": 1, "iniciada_em": 1}):
        try:
            sid = str(UUID(str(doc["_id"])))
            if not isinstance(doc["id_user"], str) or not doc["id_user"] or not doc["resumo"].strip():
                raise ValueError()
            payload = {"id_user": doc["id_user"], "session_id": sid, "resumo": doc["resumo"],
                       "iniciada_em": doc["iniciada_em"].isoformat(), "modelo_embedding": EMBEDDING_MODEL}
            records["memory"].append((sid, doc["resumo"], payload))
        except (KeyError, TypeError, ValueError, AttributeError):
            missing["memory"] += 1
    return records, missing


async def create_targets(client, targets):
    if len(set(targets.values())) != len(targets):
        raise ValueError("Os destinos devem ser distintos.")
    # Valida todos antes de criar qualquer coleção. Não reaproveita destino parcial.
    for target in targets.values():
        if "_openai_te3s_" not in target or await client.collection_exists(target):
            raise ValueError("Escolha destinos novos com _openai_te3s_; nenhuma colecao existente pode ser reutilizada.")
    for target in targets.values():
        await client.create_collection(target, vectors_config=models.VectorParams(
            size=EMBEDDING_DIMENSIONS, distance=models.Distance.COSINE))
        await client.create_payload_index(target, "modelo_embedding", models.PayloadSchemaType.KEYWORD, wait=True)
    await client.create_payload_index(targets["memory"], "id_user",
                                     models.KeywordIndexParams(type="keyword", is_tenant=True), wait=True)


async def populate(client, target, records, embed=gerar_embeddings_batch):
    for start in range(0, len(records), 50):
        batch = records[start:start + 50]
        vectors = await embed([text for _, text, _ in batch])
        if len(vectors) != len(batch):
            raise ValueError("Quantidade de vetores invalida")
        await client.upsert(target, points=[models.PointStruct(id=pid, vector=vector, payload=payload)
                                            for (pid, _, payload), vector in zip(batch, vectors, strict=True)], wait=True)
    # Validação de quantidade E de IDs/metadados, sem imprimir textos ou donos.
    actual = await read_points(client, target)
    expected = {str(pid): payload for pid, _, payload in records}
    if {str(p.id): p.payload for p in actual} != expected:
        raise ValueError("Validacao de IDs/metadados falhou; destino parcial nao deve ser ativado.")
    if (await client.count(target, exact=True)).count != len(records):
        raise ValueError("Contagem final divergente")


async def migrate(args):
    if not config.QDRANT_URL or not config.MONGODB_URI or not config.MONGODB_DATABASE:
        raise ValueError("Configure Qdrant e MongoDB para inventariar todas as fontes.")
    client = AsyncQdrantClient(url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY,
                               timeout=30, check_compatibility=False)
    mongo = AsyncMongoClient(config.MONGODB_URI, tz_aware=True, serverSelectionTimeoutMS=5000,
                             connectTimeoutMS=5000, timeoutMS=15000)
    try:
        inventory = []
        for col in (await client.get_collections()).collections:
            info = await client.get_collection(col.name)
            vectors = info.config.params.vectors
            vectors = vectors if isinstance(vectors, dict) else {"default": vectors}
            inventory.append({"collection": col.name, "points": info.points_count,
                              "dimensions": {name: v.size for name, v in vectors.items()}})
        sessions = mongo[config.MONGODB_DATABASE]["sessoes"]
        records, missing = await collect_sources(client, sessions, args.faq_source)
        legacy_memory = await read_points(client, "memoria_resumos")
        canonical = {str(pid): payload for pid, _, payload in records["memory"]}
        reconciliation = {
            "legacy_without_canonical_summary": sum(str(p.id) not in canonical for p in legacy_memory),
            "owner_mismatch": sum(str(p.id) in canonical and (p.payload or {}).get("id_user")
                                  != canonical[str(p.id)]["id_user"] for p in legacy_memory),
        }
        report = {"inventory": inventory, "sources": {k: estimate(v) for k, v in records.items()},
                  "missing_sources": missing, "memory_reconciliation": reconciliation, "applied": False}
        print(json.dumps(report, ensure_ascii=True))
        if not args.apply:
            return report
        total = sum(len(v) for v in records.values())
        cost = sum(estimate(v)["cost_usd_upper_bound"] for v in records.values())
        if (reconciliation["owner_mismatch"] or any(missing.values()) or args.expected_points != total or args.max_cost_usd is None
                or not math.isfinite(args.max_cost_usd) or args.max_cost_usd < cost):
            raise ValueError("Fontes incompletas, volume divergente ou teto de custo insuficiente. Nada foi migrado.")
        targets = {"faq": args.faq_target, "memory": args.memory_target}
        await create_targets(client, targets)
        for kind, rows in records.items():
            await populate(client, targets[kind], rows)
        print(json.dumps({"validated_targets": targets, "points": total,
                          "activation": "manual; calibre a similaridade antes de alterar as variaveis"}))
        return report
    finally:
        await client.close()
        await mongo.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-points", type=int)
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--faq-source", default="faq_chunks")
    parser.add_argument("--faq-target", default=config.QDRANT_FAQ_COLLECTION)
    parser.add_argument("--memory-target", default=config.QDRANT_MEMORY_COLLECTION)
    args = parser.parse_args()
    try:
        asyncio.run(migrate(args))
    except Exception as error:
        # Exceções dos SDKs podem conter URIs e payloads sensíveis.
        parser.exit(1, "Migracao interrompida (" + type(error).__name__ + "). Fontes preservadas; "
                    "destinos parciais nao devem ser ativados. Revise configuracao e relatorio.\n")


if __name__ == "__main__":
    main()
