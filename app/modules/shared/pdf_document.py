"""Renderização de documentos a partir de conteúdo aprovado, sem formato de chat."""

import re
from html import escape
from io import BytesIO
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit


ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_font_lock = Lock()


def _limpar_texto(value: str) -> str:
    return "".join(
        char for char in value
        if char in "\n\t" or (32 <= ord(char) <= 0xFFFF)
    ).replace("\t", "    ")


def _texto_paragrafo(value: str) -> str:
    """Aceita Markdown limitado; HTML recebido nunca é executado pelo renderer."""
    safe = escape(_limpar_texto(value))
    safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
    safe = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", safe)
    safe = re.sub(r"`([^`\n]+)`", r"\1", safe)

    def link(match):
        label, url = match.groups()
        if urlsplit(url).scheme.lower() in {"https", "http", "mailto"}:
            return f'<link href="{url}" color="#8F00C4">{label}</link>'
        return label

    return re.sub(r"\[([^\]\n]+)\]\(([^\s)]+)\)", link, safe)


def preparar_documento_pdf(conteudo: str) -> dict[str, str]:
    """Aproveita o título do conteúdo sem repetir a pergunta nem gerar novos fatos."""
    lines = conteudo.strip().splitlines()
    first = lines[0].strip() if lines else ""
    heading = re.fullmatch(r"#{1,6}\s+(.+?)\s*#*", first)
    bold = re.fullmatch(r"\*\*(.+?)\*\*", first)
    title = (heading or bold).group(1).strip() if heading or bold else ""
    if title and len(title) <= 140 and any(line.strip() for line in lines[1:]):
        return {"titulo": title, "conteudo": "\n".join(lines[1:]).strip()}
    return {"titulo": "Documento Astro", "conteudo": conteudo.strip()}


def renderizar_pdf(*, titulo: str, conteudo: str, contexto: str | None = None) -> bytes:
    """Título e conteúdo são obrigatórios; contexto aparece só quando fornecido."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

    navy = colors.HexColor("#1C1839")
    purple = colors.HexColor("#8F00C4")
    with _font_lock:
        if "AstroMuseo" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("AstroMuseo", str(ASSETS_DIR / "MuseoModerno-Medium.ttf")))
            pdfmetrics.registerFont(TTFont("AstroMuseoBold", str(ASSETS_DIR / "MuseoModerno-Bold.ttf")))
            pdfmetrics.registerFontFamily(
                "AstroMuseo", normal="AstroMuseo", bold="AstroMuseoBold",
                italic="AstroMuseo", boldItalic="AstroMuseoBold",
            )

    body = ParagraphStyle(
        "AstroBody", fontName="AstroMuseo", fontSize=10, leading=15,
        textColor=navy, spaceAfter=8, splitLongWords=True,
    )
    title = ParagraphStyle(
        "AstroTitle", parent=body, fontName="AstroMuseoBold",
        fontSize=22, leading=28, spaceAfter=18, keepWithNext=True,
    )
    section = ParagraphStyle(
        "AstroSection", parent=body, fontName="AstroMuseoBold",
        fontSize=13, leading=18, textColor=purple,
        spaceBefore=12, spaceAfter=6, keepWithNext=True,
    )
    subsection = ParagraphStyle("AstroSubsection", parent=section, fontSize=11, leading=16)
    bullet = ParagraphStyle(
        "AstroBullet", parent=body, leftIndent=18, bulletIndent=2,
        bulletFontName="AstroMuseo", bulletFontSize=9, spaceAfter=5,
    )
    note = ParagraphStyle("AstroNote", parent=body, fontSize=8, leading=12, textColor=colors.HexColor("#625C77"))
    cell = ParagraphStyle("AstroCell", parent=body, fontSize=8, leading=12, spaceAfter=0)
    header = ParagraphStyle("AstroTableHeader", parent=cell, fontName="AstroMuseoBold", textColor=colors.white)

    story = [Paragraph(_texto_paragrafo(titulo), title)]
    if contexto and contexto.strip():
        story.extend([
            Paragraph("Contexto", section),
            Paragraph(_texto_paragrafo(contexto).replace("\n", "<br/>"), body),
        ])

    def cells(line):
        return [item.strip().replace(r"\|", "|") for item in re.split(r"(?<!\\)\|", line.strip().strip("|"))]

    lines = conteudo.splitlines()
    paragraph = []

    def flush():
        if paragraph:
            story.append(Paragraph(_texto_paragrafo(" ".join(paragraph)), body))
            paragraph.clear()

    index = 0
    while index < len(lines):
        line = lines[index].strip()
        # A table is recognized only with a Markdown separator row.
        if "|" in line and index + 1 < len(lines):
            separators = cells(lines[index + 1])
            if len(separators) > 1 and all(re.fullmatch(r":?-{3,}:?", item) for item in separators):
                flush()
                rows = [cells(line)]
                index += 2
                while index < len(lines) and "|" in lines[index] and lines[index].strip():
                    rows.append(cells(lines[index]))
                    index += 1
                columns = max(map(len, rows))
                data = [
                    [Paragraph(_texto_paragrafo(value), header if n == 0 else cell)
                     for value in row + [""] * (columns - len(row))]
                    for n, row in enumerate(rows)
                ]
                table = LongTable(data, colWidths=[(A4[0] - 96) / columns] * columns, repeatRows=1, hAlign="LEFT")
                table.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), navy),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F2F9")]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 9),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("LINEBELOW", (0, 0), (-1, 0), 1, purple),
                ]))
                story.extend([table, Spacer(1, 10)])
                continue
        heading = re.fullmatch(r"(#{1,6})\s+(.+?)\s*#*", line)
        standalone_bold = re.fullmatch(r"\*\*(.+?)\*\*", line)
        item = re.match(r"^(?:([-*•])\s+|(\d+[.)])\s+)(.+)$", line)
        if not line:
            flush()
        elif heading or standalone_bold:
            flush()
            text = heading.group(2) if heading else standalone_bold.group(1)
            style = subsection if heading and len(heading.group(1)) > 2 else section
            story.append(Paragraph(_texto_paragrafo(text), style))
        elif item:
            flush()
            story.append(Paragraph(_texto_paragrafo(item.group(3)), bullet, bulletText=item.group(2) or "•"))
        elif re.fullmatch(r"(?:-{3,}|\*{3,}|_{3,})", line):
            flush()
            story.append(Spacer(1, 8))
        elif line.startswith(">") or re.match(r"^\*?(?:Fonte|Fontes|Referência|Referências):", line, re.IGNORECASE):
            flush()
            story.append(Paragraph(_texto_paragrafo(line.lstrip("> ")), note))
        else:
            paragraph.append(line)
        index += 1
    flush()

    stream = BytesIO()
    document = SimpleDocTemplate(
        stream, pagesize=A4, leftMargin=48, rightMargin=48,
        topMargin=102, bottomMargin=56, title=_limpar_texto(titulo), author="Astro",
    )
    logo = ImageReader(str(ASSETS_DIR / "astro-logo.png"))

    def page_decoration(canvas, doc):
        canvas.saveState()
        width, height = A4
        canvas.setFillColor(navy)
        canvas.rect(0, height - 6, width, 6, fill=1, stroke=0)
        canvas.setFillColor(purple)
        canvas.rect(0, height - 6, 164, 6, fill=1, stroke=0)
        canvas.setFont("AstroMuseoBold", 8)
        canvas.setFillColor(navy)
        canvas.drawString(48, height - 44, "ASTRO  /  DOCUMENTO")
        canvas.saveState()
        canvas.setFillAlpha(0.16)
        canvas.drawImage(logo, width - 112, height - 76, width=64, height=50, preserveAspectRatio=True, mask="auto")
        canvas.restoreState()
        canvas.setStrokeColor(purple)
        canvas.setLineWidth(0.7)
        canvas.line(48, height - 86, width - 48, height - 86)
        canvas.setStrokeColor(colors.HexColor("#DDD8E7"))
        canvas.line(48, 40, width - 48, 40)
        canvas.setFont("AstroMuseo", 8)
        canvas.setFillColor(navy)
        canvas.drawString(48, 25, "Astro - documento gerado sob solicitação")
        canvas.drawRightString(width - 48, 25, f"Página {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=page_decoration, onLaterPages=page_decoration)
    return stream.getvalue()
