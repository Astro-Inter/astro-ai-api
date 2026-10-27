"""Ingere PDFs em uma coleção NOVA. Sem --apply apenas extrai e estima custo."""
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


COLLECTION_FAQ = config.QDRANT_FAQ_COLLECTION
CHUNK_SIZE = 700
CHUNK_OVERLAP = 150


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


async def ingerir_faq(entradas: list[str | Path], *, client=None, embed_batch=None,
                      target: str = COLLECTION_FAQ) -> int:
    """Nunca substitui dados existentes. Após falha use outro destino e mantenha a origem."""
    from app.scripts.migrate_vectors import populate
    chunks = carregar_chunks(entradas)
    if "_openai_te3s_" not in target:
        raise ValueError("Use uma colecao nova com _openai_te3s_.")
    owns_client = client is None
    if owns_client:
        if not config.QDRANT_URL:
            raise ValueError("Configure QDRANT_URL.")
        client = AsyncQdrantClient(url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY,
                                   timeout=30, check_compatibility=False)
    try:
        if await client.collection_exists(target):
            raise ValueError("O destino ja existe; escolha outro nome. Nenhum ponto foi apagado.")
        await client.create_collection(target, vectors_config=models.VectorParams(
            size=EMBEDDING_DIMENSIONS, distance=models.Distance.COSINE))
        await client.create_payload_index(target, "modelo_embedding", models.PayloadSchemaType.KEYWORD, wait=True)
        await populate(client, target, [(str(uuid4()), c["page_content"], c) for c in chunks],
                       embed=embed_batch or gerar_embeddings_batch)
        return len(chunks)
    except Exception:
        raise RuntimeError("Falha na ingestao. Fontes preservadas; destino pode estar vazio ou parcial. "
                           "Nao ative esse destino; repita em uma NOVA colecao.") from None
    finally:
        if owns_client:
            await client.close()


def main():
    from app.scripts.migrate_vectors import estimate
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arquivos", nargs="+", help="PDFs ou pastas contendo PDFs")
    parser.add_argument("--target", default=COLLECTION_FAQ)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-points", type=int)
    parser.add_argument("--max-cost-usd", type=float)
    args = parser.parse_args()
    try:
        chunks = carregar_chunks(args.arquivos)
        report = estimate([(None, c["page_content"], c) for c in chunks])
        print(report)
        if args.apply:
            import math
            if (args.expected_points != len(chunks) or args.max_cost_usd is None
                    or not math.isfinite(args.max_cost_usd)
                    or args.max_cost_usd < report["cost_usd_upper_bound"]):
                raise ValueError("Confirme volume e teto de custo antes de aplicar.")
            asyncio.run(ingerir_faq(args.arquivos, target=args.target))
    except (ValueError, RuntimeError) as error:
        parser.exit(1, f"[ingest] ERRO: {error}\n")


if __name__ == "__main__":
    main()
