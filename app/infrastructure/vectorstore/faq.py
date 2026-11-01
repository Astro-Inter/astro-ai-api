from pathlib import PureWindowsPath
import logging

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.core import config
from app.infrastructure.llm.embeddings import (
    EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, gerar_embeddings_batch,
)
from app.modules.chat.errors import ChatError


COLLECTION_FAQ = config.QDRANT_FAQ_COLLECTION
# A calibração do corpus real encontrou a evidência de público-alvo na sexta posição.
FAQ_RESULT_LIMIT = 6
FAQ_SCORE_THRESHOLD = config.FAQ_SCORE_THRESHOLD
MAX_CHUNK_CHARACTERS = 2000


def _fonte_publica(source) -> str:
    """Remove diretórios Windows e POSIX sem depender do sistema operacional."""
    if not isinstance(source, str):
        return "Documento FAQ"
    # A sintaxe Windows reconhece tanto barras quanto contrabarras, inclusive
    # unidades e caminhos UNC. PurePath escolheria a sintaxe do host do serviço.
    return PureWindowsPath(source.strip()).name or "Documento FAQ"


class FaqVectors:
    """Consulta somente-leitura aos trechos oficiais ingeridos no Qdrant."""

    def __init__(self):
        self.client = None
        self.vector_name = None
        self.ready = False

    async def connect(self):
        if "_openai_te3s_" not in COLLECTION_FAQ:
            raise ChatError(503, "Use uma colecao FAQ nova para text-embedding-3-small.")
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
                    503, "Colecao FAQ incompativel com openai/text-embedding-3-small (1536/Cosine).",
                )
            self.ready = True
        return self.client

    async def search(self, query: str) -> list[dict]:
        stage = "faq_metadata"
        try:
            client = await self.connect()
            stage = "faq_embeddings"
            vector = (await gerar_embeddings_batch([query]))[0]
            stage = "faq_query"
            result = await client.query_points(
                COLLECTION_FAQ,
                query=vector,
                using=self.vector_name,
                query_filter=models.Filter(must=[models.FieldCondition(
                    key="modelo_embedding", match=models.MatchValue(value=EMBEDDING_MODEL))]),
                limit=FAQ_RESULT_LIMIT,
                score_threshold=FAQ_SCORE_THRESHOLD,
                with_payload=True,
                with_vectors=False,
            )
            stage = "faq_payload"
            snippets = []
            for point in result.points:
                payload = point.payload or {}
                content = payload.get("page_content")
                source = payload.get("source")
                page = payload.get("page_number")
                if not isinstance(content, str) or not content.strip():
                    continue
                # Evita divulgar caminhos locais eventualmente gravados por uma ingestao antiga.
                source = _fonte_publica(source)
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
        except UnexpectedResponse as error:
            # Driver messages may contain endpoint credentials or payloads.
            # Only classify controlled status/stage, never copy response bodies.
            missing = stage == "faq_metadata" and error.status_code == 404
            reason = "faq_collection_missing" if missing else "faq_vector_service_failure"
            from app.diagnostics import diagnostic
            fields = {**diagnostic(error), "event": "faq_search_failed", "operation": stage,
                      "status": error.status_code,
                      "error_code": "FAQ_COLLECTION_MISSING" if missing else "FAQ_VECTOR_SERVICE_FAILURE"}
            logging.getLogger(__name__).warning("Consulta FAQ indisponivel", extra=fields)
            raise ChatError(503, "Consulta as normas indisponivel.", reason=reason, stage=stage) from None
        except Exception as error:
            from app.diagnostics import diagnostic
            logging.getLogger(__name__).warning("Consulta FAQ indisponivel",extra={**diagnostic(error), "event": "faq_search_failed", "operation": stage})
            raise ChatError(503, "Consulta as normas indisponivel.", reason="faq_dependency_failure", stage=stage) from None

    async def close(self):
        if self.client is not None:
            await self.client.close()
