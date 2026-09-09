"""Substitui TODOS os pontos de faq_chunks pelos PDFs fornecidos.

    python -m app.scripts.ingest_faq "C:\\documentos\\faq.pdf"
    python -m app.scripts.ingest_faq "C:\\documentos\\faq"

Valida PDFs, coleção e embeddings antes da limpeza. Não execute em paralelo.
Não altera a coleção memoria_resumos.
"""
import argparse
import asyncio
from pathlib import Path
from uuid import uuid4

from pypdf import PdfReader
from qdrant_client import AsyncQdrantClient, models

from app.core import config
from app.infrastructure.llm.embeddings import (
    EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, gerar_embeddings_batch,
)
from app.modules.chat.errors import ChatError


COLLECTION_FAQ = "faq_chunks"
CHUNK_SIZE = 700
CHUNK_OVERLAP = 150
BATCH_SIZE = 50


def dividir_texto(texto: str) -> list[str]:
    """Janelas de até 700 caracteres; prefere parágrafos, linhas e palavras."""
    chunks = []
    inicio = 0
    while inicio < len(texto):
        fim = min(inicio + CHUNK_SIZE, len(texto))
        if fim < len(texto):
            # Evita fragmentos pequenos e mantém progresso maior que a sobreposição.
            for separador in ("\n\n", "\n", " "):
                quebra = texto.rfind(separador, inicio + CHUNK_SIZE // 2, fim)
                if quebra >= 0:
                    fim = quebra + len(separador)
                    break
        chunk = texto[inicio:fim].strip()
        if chunk:
            chunks.append(chunk)
        if fim == len(texto):
            break
        inicio = fim - CHUNK_OVERLAP
    return chunks


def carregar_chunks(entradas: list[str | Path]) -> list[dict]:
    arquivos = set()
    for entrada in entradas:
        path = Path(entrada).expanduser().resolve()
        if path.is_dir():
            arquivos.update(p.resolve() for p in path.iterdir()
                            if p.is_file() and p.suffix.lower() == ".pdf")
        elif path.is_file() and path.suffix.lower() == ".pdf":
            arquivos.add(path)
        else:
            raise ValueError(f"PDF ou pasta nao encontrado: {path.name}")
    if not arquivos:
        raise ValueError("Nenhum PDF encontrado. A colecao nao foi alterada.")
    chunks = []
    for path in sorted(arquivos):
        inicio = len(chunks)
        print(f"[ingest] Carregando: {path.name}")
        try:
            with path.open("rb") as stream:
                reader = PdfReader(stream)
                if reader.is_encrypted:
                    raise ValueError("PDF protegido")
                for page_number, page in enumerate(reader.pages):
                    text = (page.extract_text() or "").strip()
                    if not text:
                        print(f"[ingest] Aviso: pagina {page_number + 1} sem texto em {path.name}.")
                    for chunk in dividir_texto(text):
                        chunks.append({"page_content": chunk, "page_number": page_number,
                                       "source": path.name, "modelo_embedding": EMBEDDING_MODEL})
        except Exception:
            raise ValueError(f"Nao foi possivel extrair o PDF: {path.name}. Colecao preservada.") from None
        if len(chunks) == inicio:
            raise ValueError(f"PDF sem texto extraivel: {path.name}. Aplique OCR antes de ingerir.")
    print(f"[ingest] {len(arquivos)} PDF(s); {len(chunks)} chunk(s).")
    return chunks


def validar_colecao(info):
    vectors = info.config.params.vectors
    vector_name = None
    if isinstance(vectors, dict):
        if len(vectors) != 1:
            raise ValueError("faq_chunks deve ter um unico vetor denso.")
        vector_name, vectors = next(iter(vectors.items()))
    if (vectors is None or vectors.size != EMBEDDING_DIMENSIONS
            or vectors.distance != models.Distance.COSINE):
        raise ValueError("faq_chunks precisa usar 1024 dimensoes e Cosine. Nada foi apagado.")
    return vector_name


async def ingerir_faq(entradas: list[str | Path], *, client=None, embed_batch=None) -> int:
    """Extrai PDFs, prepara os vetores e substitui toda a base FAQ. Retorna chunks."""
    chunks = carregar_chunks(entradas)
    embed_batch = embed_batch or gerar_embeddings_batch
    owns_client = client is None
    if owns_client:
        if not config.QDRANT_URL:
            raise ValueError("Configure QDRANT_URL no .env.")
        client = AsyncQdrantClient(url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY,
                                   timeout=30, check_compatibility=False)
    cleanup_started = False
    try:
        name = validar_colecao(await client.get_collection(COLLECTION_FAQ))
        points = []
        for i in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[i:i + BATCH_SIZE]
            print(f"[ingest] Gerando embeddings: {i + 1}-{i + len(batch)}")
            vectors = await embed_batch([chunk["page_content"] for chunk in batch])
            if len(vectors) != len(batch):
                raise ValueError("Quantidade de embeddings diferente da quantidade de chunks.")
            points.extend(models.PointStruct(
                id=str(uuid4()), vector={name: vector} if name is not None else vector,
                payload=chunk,
            ) for chunk, vector in zip(batch, vectors, strict=True))

        old_count = (await client.count(COLLECTION_FAQ, exact=True)).count
        print(f"[ingest] Apagando TODOS os {old_count} ponto(s) de {COLLECTION_FAQ}...")
        cleanup_started = True
        await client.delete(COLLECTION_FAQ, points_selector=models.FilterSelector(
            filter=models.Filter(must=[])), wait=True)
        if (await client.count(COLLECTION_FAQ, exact=True)).count != 0:
            raise ValueError("Colecao nao ficou vazia apos a limpeza.")
        for i in range(0, len(points), BATCH_SIZE):
            await client.upsert(COLLECTION_FAQ, points=points[i:i + BATCH_SIZE], wait=True)
        if (await client.count(COLLECTION_FAQ, exact=True)).count != len(points):
            raise ValueError("Contagem final diferente da quantidade enviada.")
        print(f"[ingest] Concluido: {len(points)} chunk(s) em {COLLECTION_FAQ}.")
        return len(points)
    except Exception as error:
        if cleanup_started:
            message = "Falha durante a substituicao. faq_chunks pode estar vazia ou parcial; " \
                      "execute novamente com TODOS os PDFs. Nao ha rollback automatico."
        else:
            message = "Falha na preparacao. Nenhum ponto existente foi apagado."
        if isinstance(error, (ValueError, ChatError)):
            message += " " + str(error)
        raise RuntimeError(message) from None
    finally:
        if owns_client:
            await client.close()


def main():
    parser = argparse.ArgumentParser(description=(
        "Substitui TODOS os pontos de faq_chunks pelos PDFs informados. "
        "Pastas incluem apenas PDFs do primeiro nivel. Envie a base completa a cada execucao."
    ))
    parser.add_argument("arquivos", nargs="+", help="PDFs ou pastas contendo PDFs")
    args = parser.parse_args()
    try:
        asyncio.run(ingerir_faq(args.arquivos))
    except (ValueError, RuntimeError) as error:
        parser.exit(1, f"[ingest] ERRO: {error}\n")
    except KeyboardInterrupt:
        parser.exit(130, "[ingest] Interrompido. Confira a colecao antes de usar o FAQ.\n")


if __name__ == "__main__":
    main()
