import math
from uuid import UUID

import httpx
from qdrant_client import AsyncQdrantClient, models

from app.core import config
from app.modules.chat.errors import ChatError


COLLECTION_MEMORY = "memoria_resumos"
EMBEDDING_MODEL = "mistral-embed"
EMBEDDING_DIMENSIONS = 1024


class SummaryVectors:
    def __init__(self):
        self.client = None
        self.vector_name = None
        self.ready = False

    async def connect(self):
        if not self.ready:
            if not config.QDRANT_URL:
                raise ChatError(503, "Qdrant nao configurado.")
            if self.client is None:
                self.client = AsyncQdrantClient(
                    url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY,
                    timeout=10, check_compatibility=False,
                )
            info = await self.client.get_collection(COLLECTION_MEMORY)
            vectors = info.config.params.vectors
            if isinstance(vectors, dict):
                if len(vectors) != 1:
                    raise ChatError(503, "Colecao de memoria deve ter um unico vetor denso.")
                self.vector_name, vector = next(iter(vectors.items()))
            else:
                vector = vectors
            if (vector is None or vector.size != EMBEDDING_DIMENSIONS
                    or vector.distance != models.Distance.COSINE):
                raise ChatError(503, "Colecao de memoria incompativel com mistral-embed (1024/Cosine).")
            if "id_user" not in info.payload_schema:
                await self.client.create_payload_index(
                    COLLECTION_MEMORY, "id_user",
                    models.KeywordIndexParams(type="keyword", is_tenant=True), wait=True,
                )
            self.ready = True
        return self.client

    async def embed(self, text: str):
        if not config.MISTRAL_API_KEY:
            raise ChatError(503, "Mistral nao configurada para embeddings.")
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response = await client.post(
                "https://api.mistral.ai/v1/embeddings",
                headers={"Authorization": f"Bearer {config.MISTRAL_API_KEY}"},
                json={"model": EMBEDDING_MODEL, "input": [text], "encoding_format": "float"},
            )
            response.raise_for_status()
            vector = response.json()["data"][0]["embedding"]
        if (not isinstance(vector, list) or len(vector) != EMBEDDING_DIMENSIONS
                or any(type(value) not in (float, int) or not math.isfinite(value) for value in vector)
                or not any(vector)):
            raise ChatError(502, "Embedding de memoria invalido.")
        return vector

    async def upsert(self, doc: dict):
        try:
            client = await self.connect()
            vector = await self.embed(doc["resumo"])
            await client.upsert(COLLECTION_MEMORY, points=[models.PointStruct(
                id=doc["_id"],
                vector={self.vector_name: vector} if self.vector_name is not None else vector,
                payload={
                    "id_user": doc["id_user"], "session_id": doc["_id"],
                    "resumo": doc["resumo"], "modelo_embedding": EMBEDDING_MODEL,
                    "iniciada_em": doc["iniciada_em"].isoformat(),
                },
            )], wait=True)
        except ChatError:
            raise
        except Exception:
            raise ChatError(503, "Indexacao do resumo indisponivel. Repita o encerramento.") from None

    async def search(self, uid: str, exclude: str, query: str):
        try:
            client = await self.connect()
            vector = await self.embed(query)
            result = await client.query_points(
                COLLECTION_MEMORY, query=vector, using=self.vector_name,
                query_filter=models.Filter(
                    must=[models.FieldCondition(key="id_user", match=models.MatchValue(value=uid))],
                    must_not=[models.HasIdCondition(has_id=[exclude])],
                ), limit=3, with_payload=False, with_vectors=False,
            )
            return [str(UUID(str(point.id))) for point in result.points]
        except ChatError:
            raise
        except Exception:
            raise ChatError(503, "Busca semantica de conversas indisponivel.") from None

    async def close(self):
        if self.client is not None:
            await self.client.close()
