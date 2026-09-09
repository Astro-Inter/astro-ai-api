import math

import httpx

from app.core import config
from app.modules.chat.errors import ChatError


EMBEDDING_MODEL = "mistral-embed"
EMBEDDING_DIMENSIONS = 1024


async def gerar_embeddings_batch(textos: list[str]) -> list[list[float]]:
    """Gera embeddings na ordem dos textos; o chamador divide os lotes."""
    if not textos or any(not text.strip() for text in textos):
        raise ChatError(422, "Informe textos nao vazios para gerar embeddings.")
    if not config.MISTRAL_API_KEY:
        raise ChatError(503, "Mistral nao configurada para embeddings.")
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response = await client.post(
                "https://api.mistral.ai/v1/embeddings",
                headers={"Authorization": f"Bearer {config.MISTRAL_API_KEY}"},
                json={"model": EMBEDDING_MODEL, "input": textos, "encoding_format": "float"},
            )
            response.raise_for_status()
            data = response.json()["data"]
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
    except httpx.HTTPError:
        raise ChatError(503, "Servico de embeddings indisponivel.") from None
    except (ValueError, KeyError, TypeError):
        raise ChatError(502, "Resposta de embeddings invalida.") from None
