"""Ferramentas compartilhadas pelos especialistas do chat."""

import re
import logging
import unicodedata
from urllib.parse import urlparse
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from app.core import config as app_config
from app.core.security import CurrentUser
from app.modules.shared.pdf_document import renderizar_pdf


PDF_LINK_TTL_HOURS = 5
PDF_LINK_TTL_SECONDS = PDF_LINK_TTL_HOURS * 3600
MAX_PDF_BYTES = 2_000_000
logger = logging.getLogger(__name__)


class GerarPdfArgs(BaseModel):
    """Conteúdo aprovado pela aplicação; não é preenchido livremente pelo LLM."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    titulo: str = Field(min_length=1, max_length=140)
    conteudo: str = Field(min_length=1, max_length=12000)
    contexto: str | None = Field(default=None, min_length=1, max_length=4000)


def _usuario_do_contexto(runtime_config: RunnableConfig) -> CurrentUser | None:
    raw_user = (runtime_config or {}).get("configurable", {}).get("usuario_atual")
    try:
        return raw_user if isinstance(raw_user, CurrentUser) else CurrentUser.model_validate(raw_user)
    except Exception:
        return None


def _nome_arquivo(titulo: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")[:80].strip("-")
    return f"{slug or 'consulta-astro'}.pdf"


def get_r2_client():
    """Usa a API S3 do R2 sem expor credenciais ao modelo ou ao cliente."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=app_config.R2_ENDPOINT,
        aws_access_key_id=app_config.R2_ACCESS_KEY_ID,
        aws_secret_access_key=app_config.R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}),
    )


def _r2_configurado() -> bool:
    if not all((
        app_config.R2_ACCESS_KEY_ID, app_config.R2_SECRET_ACCESS_KEY,
        app_config.R2_ENDPOINT, app_config.R2_BUCKET_NAME,
    )):
        return False
    endpoint = urlparse(app_config.R2_ENDPOINT)
    return (
        endpoint.scheme == "https"
        and bool(endpoint.hostname)
        and endpoint.hostname.endswith(".r2.cloudflarestorage.com")
        and not endpoint.username and not endpoint.password
        and endpoint.path in {"", "/"}
        and not endpoint.query and not endpoint.fragment
    )


@tool("gerar_pdf", args_schema=GerarPdfArgs)
def gerar_pdf(
    titulo: str,
    conteudo: str,
    contexto: str | None = None,
    config: RunnableConfig = None,
) -> dict:
    """Gera documento com título, conteúdo e contexto opcional, com link temporário.

    O grafo chama esta ferramenta somente após Juiz e guardrail de saída. O
    modelo não escolhe o conteúdo nem uma chave de objeto no bucket.
    """
    if _usuario_do_contexto(config) is None:
        return {"status": "erro", "mensagem": "Usuario nao identificado no contexto."}
    if not _r2_configurado():
        return {"status": "indisponivel", "mensagem": "Geracao de PDF indisponivel."}

    etapa = "renderizacao"
    try:
        pdf_bytes = renderizar_pdf(titulo=titulo, conteudo=conteudo, contexto=contexto)
        if not pdf_bytes.startswith(b"%PDF-") or len(pdf_bytes) > MAX_PDF_BYTES:
            return {"status": "indisponivel", "mensagem": "Geracao de PDF indisponivel."}
        key = f"consultas/{uuid4()}.pdf"
        client = get_r2_client()
        etapa = "assinatura"
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": app_config.R2_BUCKET_NAME, "Key": key},
            ExpiresIn=PDF_LINK_TTL_SECONDS,
        )
        etapa = "upload"
        client.put_object(
            Bucket=app_config.R2_BUCKET_NAME,
            Key=key,
            Body=pdf_bytes,
            ContentType="application/pdf",
            ContentDisposition=f'attachment; filename="{_nome_arquivo(titulo)}"',
        )
    except Exception as exc:
        # Nunca registrar a exceção completa: pode conter URL assinada ou dados.
        response = getattr(exc, "response", None)
        code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
        logger.warning("PDF indisponivel etapa=%s codigo=%s", etapa, code or type(exc).__name__)
        return {"status": "indisponivel", "mensagem": "Geracao de PDF indisponivel."}
    return {"status": "ok", "url": url, "expira_em_segundos": PDF_LINK_TTL_SECONDS}


TOOLS_COMPARTILHADAS = [gerar_pdf]
