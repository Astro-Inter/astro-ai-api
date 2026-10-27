import math

from app.infrastructure.llm.openrouter import request

from app.core.pii import redact_for_embedding
from app.modules.chat.errors import ChatError


EMBEDDING_MODEL = "openai/text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536


async def gerar_embeddings_batch(textos: list[str]) -> list[list[float]]:
    """Gera embeddings na ordem dos textos; o chamador divide os lotes."""
    if not textos or any(not text.strip() for text in textos):
        raise ChatError(422, "Informe textos nao vazios para gerar embeddings.")
    try:
        response = await request("/v1/embeddings", {
            "model": EMBEDDING_MODEL,
            "input": [redact_for_embedding(text) for text in textos],
            "encoding_format": "float", "dimensions": EMBEDDING_DIMENSIONS,
            "provider": {"data_collection": "deny"},
        }, stage="embeddings")
        data = response["data"]
        if len(data) != len(textos):
            raise ValueError("Quantidade de embeddings inesperada")
        vectors = [None] * len(textos)
        for item in data:
            # Compatibilidade com resposta unitária sem índice; lotes exigem índice.
            index = item.get("index", 0 if len(textos) == 1 else None)
            vector = item["embedding"]
            if (type(index) is not int or not 0 <= index < len(textos)
                    or vectors[index] is not None or not isinstance(vector, list)
                    or len(vector) != EMBEDDING_DIMENSIONS
                    or any(type(v) not in (float, int) or not math.isfinite(v) for v in vector)
                    or not any(vector)):
                raise ValueError("Embedding invalido")
            vectors[index] = vector
        return vectors
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ChatError(502, "Resposta de embeddings invalida.") from None
