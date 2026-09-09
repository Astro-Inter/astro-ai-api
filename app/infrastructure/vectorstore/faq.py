from pathlib import PurePath

from qdrant_client import AsyncQdrantClient, models

from app.core import config
from app.infrastructure.llm.embeddings import (
    EMBEDDING_DIMENSIONS, gerar_embeddings_batch,
)
from app.modules.chat.errors import ChatError


COLLECTION_FAQ = "faq_chunks"
FAQ_RESULT_LIMIT = 5
FAQ_SCORE_THRESHOLD = 0.35
MAX_CHUNK_CHARACTERS = 2000


class FaqVectors:
    """Consulta somente-leitura aos trechos oficiais ingeridos no Qdrant."""

    def __init__(self):
        self.client = None
        self.vector_name = None
        self.ready = False

    async def connect(self):
        if not self.ready:
            if not config.QDRANT_URL:
                raise ChatError(503, "Consulta as normas indisponivel.")
            if self.client is None:
                self.client = AsyncQdrantClient(
                    url=config.QDRANT_URL,
                    api_key=config.QDRANT_API_KEY,
                    timeout=10,
                    check_compatibility=False,
                )
            info = await self.client.get_collection(COLLECTION_FAQ)
            vectors = info.config.params.vectors
            if isinstance(vectors, dict):
                if len(vectors) != 1:
                    raise ChatError(503, "Colecao FAQ deve ter um unico vetor denso.")
                self.vector_name, vector = next(iter(vectors.items()))
            else:
                vector = vectors
            if (vector is None or vector.size != EMBEDDING_DIMENSIONS
                    or vector.distance != models.Distance.COSINE):
                raise ChatError(
                    503, "Colecao FAQ incompativel com mistral-embed (1024/Cosine).",
                )
            self.ready = True
        return self.client

    async def search(self, query: str) -> list[dict]:
        try:
            client = await self.connect()
            vector = (await gerar_embeddings_batch([query]))[0]
            result = await client.query_points(
                COLLECTION_FAQ,
                query=vector,
                using=self.vector_name,
                limit=FAQ_RESULT_LIMIT,
                score_threshold=FAQ_SCORE_THRESHOLD,
                with_payload=True,
                with_vectors=False,
            )
            snippets = []
            for point in result.points:
                payload = point.payload or {}
                content = payload.get("page_content")
                source = payload.get("source")
                page = payload.get("page_number")
                if not isinstance(content, str) or not content.strip():
                    continue
                # Evita divulgar caminhos locais eventualmente gravados por uma ingestao antiga.
                source = PurePath(source).name if isinstance(source, str) else "Documento FAQ"
                page = page + 1 if type(page) is int and page >= 0 else None
                snippets.append({
                    "conteudo": content.strip()[:MAX_CHUNK_CHARACTERS],
                    "fonte": source,
                    "pagina": page,
                    "relevancia": round(float(point.score), 4),
                })
            return snippets
        except ChatError:
            raise
        except Exception:
            raise ChatError(503, "Consulta as normas indisponivel.") from None

    async def close(self):
        if self.client is not None:
            await self.client.close()
