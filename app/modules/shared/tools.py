"""Ferramentas compartilhadas pelos especialistas do chat."""

import re
import logging
import unicodedata
from html import escape
from io import BytesIO
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from app.core import config as app_config
from app.core.security import CurrentUser


PDF_LINK_TTL_HOURS = 5
PDF_LINK_TTL_SECONDS = PDF_LINK_TTL_HOURS * 3600
MAX_PDF_BYTES = 2_000_000
PDF_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_font_lock = Lock()
logger = logging.getLogger(__name__)


class GerarPdfArgs(BaseModel):
    """Conteúdo aprovado pela aplicação; não é preenchido livremente pelo LLM."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    titulo: str = Field(min_length=1, max_length=140)
    pergunta: str = Field(min_length=1, max_length=4000)
    resposta: str = Field(min_length=1, max_length=12000)


def _usuario_do_contexto(runtime_config: RunnableConfig) -> CurrentUser | None:
    raw_user = (runtime_config or {}).get("configurable", {}).get("usuario_atual")
    try:
        return raw_user if isinstance(raw_user, CurrentUser) else CurrentUser.model_validate(raw_user)
    except Exception:
        return None


def _limpar_texto(value: str) -> str:
    """Remove controles e glifos fora do plano suportado pela fonte do PDF."""
    return "".join(
        char for char in value
        if char in "\n\t" or (ord(char) >= 32 and ord(char) <= 0xFFFF)
    ).replace("\t", "    ")


def _texto_paragrafo(value: str) -> str:
    safe = escape(_limpar_texto(value))
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)


def _nome_arquivo(titulo: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")[:80].strip("-")
    return f"{slug or 'consulta-astro'}.pdf"


def renderizar_pdf(*, titulo: str, pergunta: str, resposta: str) -> bytes:
    """Monta um relatório legível a partir da pergunta e da resposta validada."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    navy = colors.HexColor("#1C1839")
    purple = colors.HexColor("#8F00C4")
    with _font_lock:
        if "AstroMuseo" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(
                "AstroMuseo", str(PDF_ASSETS_DIR / "MuseoModerno-Medium.ttf"),
            ))
            pdfmetrics.registerFont(TTFont(
                "AstroMuseoBold", str(PDF_ASSETS_DIR / "MuseoModerno-Bold.ttf"),
            ))
            pdfmetrics.registerFontFamily(
                "AstroMuseo", normal="AstroMuseo", bold="AstroMuseoBold",
            )

    body_style = ParagraphStyle(
        "AstroBody", fontName="AstroMuseo", fontSize=10, leading=15,
        textColor=navy, alignment=TA_LEFT,
        wordWrap="CJK", spaceAfter=7,
    )
    title_style = ParagraphStyle(
        "AstroTitle", parent=body_style, fontName="AstroMuseoBold",
        fontSize=18, leading=24, textColor=navy, spaceAfter=18,
    )
    section_style = ParagraphStyle(
        "AstroSection", parent=body_style, fontName="AstroMuseoBold",
        fontSize=11, leading=16, textColor=purple,
        spaceBefore=13, spaceAfter=6,
    )
    bullet_style = ParagraphStyle(
        "AstroBullet", parent=body_style, leftIndent=14, firstLineIndent=-9,
    )
    story = [Paragraph(_texto_paragrafo(titulo), title_style)]
    story.append(Paragraph("Pergunta", section_style))
    story.append(Paragraph(_texto_paragrafo(pergunta), body_style))
    story.append(Paragraph("Resposta", section_style))
    for line in resposta.splitlines():
        stripped = line.strip()
        if not stripped:
            story.append(Spacer(1, 6))
        elif stripped.startswith(("- ", "* ")):
            story.append(Paragraph("• " + _texto_paragrafo(stripped[2:]), bullet_style))
        elif stripped.startswith("# "):
            story.append(Paragraph(_texto_paragrafo(stripped[2:]), section_style))
        else:
            story.append(Paragraph(_texto_paragrafo(stripped), body_style))

    stream = BytesIO()
    document = SimpleDocTemplate(
        stream, pagesize=A4, leftMargin=48, rightMargin=48,
        topMargin=112, bottomMargin=56,
        title=_limpar_texto(titulo), author="Astro",
    )
    logo = ImageReader(str(PDF_ASSETS_DIR / "astro-logo.png"))

    def page_decoration(canvas, doc):
        canvas.saveState()
        width, height = A4
        canvas.setFillColor(navy)
        canvas.rect(0, height - 8, width, 8, fill=1, stroke=0)
        canvas.setFillColor(purple)
        canvas.rect(0, height - 8, 164, 8, fill=1, stroke=0)
        canvas.setFont("AstroMuseoBold", 8)
        canvas.setFillColor(navy)
        canvas.drawString(48, height - 48, "ASTRO  /  CONSULTA")
        canvas.saveState()
        canvas.setFillAlpha(0.16)
        canvas.drawImage(
            logo, width - 120, height - 88, width=72, height=57,
            preserveAspectRatio=True, mask="auto",
        )
        canvas.restoreState()
        canvas.setStrokeColor(purple)
        canvas.setLineWidth(0.7)
        canvas.line(48, height - 101, width - 48, height - 101)
        canvas.setStrokeColor(navy)
        canvas.setLineWidth(0.5)
        canvas.line(48, 40, width - 48, 40)
        canvas.setFont("AstroMuseo", 8)
        canvas.setFillColor(navy)
        canvas.drawString(48, 25, "Astro - consulta gerada a pedido do usuário")
        canvas.drawRightString(width - 48, 25, f"Página {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=page_decoration, onLaterPages=page_decoration)
    return stream.getvalue()


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
    pergunta: str,
    resposta: str,
    config: RunnableConfig = None,
) -> dict:
    """Gera PDF da resposta revisada e envia ao R2 com link privado temporário.

    O grafo chama esta ferramenta somente após Juiz e guardrail de saída. O
    modelo não escolhe o conteúdo nem uma chave de objeto no bucket.
    """
    if _usuario_do_contexto(config) is None:
        return {"status": "erro", "mensagem": "Usuario nao identificado no contexto."}
    if not _r2_configurado():
        return {"status": "indisponivel", "mensagem": "Geracao de PDF indisponivel."}

    etapa = "renderizacao"
    try:
        pdf_bytes = renderizar_pdf(titulo=titulo, pergunta=pergunta, resposta=resposta)
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
