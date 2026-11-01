"""Build the reviewed product FAQ PDF and citation-preserving records, offline."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from xml.sax.saxutils import escape

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, KeepTogether

from app.infrastructure.llm.embeddings import EMBEDDING_MODEL

SOURCE = Path('docs/faq/astro-faq-funcional.md')
PDF = Path('output/pdf/Astro_FAQ_Funcional_v2.0.pdf')
ASSETS = Path(__file__).resolve().parents[1] / 'modules/shared/assets'


def questions(text):
    entries = []
    for match in re.finditer(r'^### (F\d{2}) — ([^\n]+)\n(.*?)(?=^### |^## |\Z)', text, re.M | re.S):
        identifier, title, body = match.groups()
        answer, separator, reference = body.strip().rpartition('\nFonte: ')
        if not separator or not answer.strip() or not reference.strip():
            raise ValueError('Cada resposta exige fonte explícita.')
        content = f'{title}\n{answer.strip()}\nFonte: {reference.strip()}'
        if len(content) > 1800:
            raise ValueError('Pergunta excede o orçamento de recuperação.')
        entries.append({'faq_id': identifier, 'question': title, 'answer': answer.strip(),
                        'reference': reference.strip(), 'page_content': content})
    if not entries or len({e['faq_id'] for e in entries}) != len(entries):
        raise ValueError('FAQ vazio ou identificadores duplicados.')
    return entries


def build(source=SOURCE, output=PDF):
    text = Path(source).read_text(encoding='utf-8-sig')
    entries = questions(text)
    for font, filename in [('AstroFAQ', 'MuseoModerno-Medium.ttf'), ('AstroFAQBold', 'MuseoModerno-Bold.ttf')]:
        if font not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(font, str(ASSETS / filename)))
    body = ParagraphStyle('Body', fontName='AstroFAQ', fontSize=10, leading=15, textColor=colors.HexColor('#25253b'), spaceAfter=8)
    heading = ParagraphStyle('Heading', parent=body, fontName='AstroFAQBold', fontSize=12, leading=17, spaceBefore=12)
    title = ParagraphStyle('Title', parent=heading, fontSize=24, leading=30, spaceAfter=16)
    ref = ParagraphStyle('Reference', parent=body, fontSize=8, leading=12, textColor=colors.HexColor('#5c536f'))
    story = [Paragraph('ASTRO | FAQ funcional', title), Paragraph('Versão 2.0 • Revisão editorial: 01/11/2026', body),
             Paragraph('Guia de consulta baseado no memorial funcional de 23/09/2026. Não é nova política jurídica nem comprovação de que todos os recursos já estejam publicados.', body)]
    entry_map = {e['faq_id']: e for e in entries}
    # Preserve the authored topic order. Keep each question/answer/reference on one page.
    for line in text.splitlines():
        if line.startswith('## ') and line != '## Referências consultadas':
            story.append(Paragraph(escape(line[3:]), heading))
        if line.startswith('### '):
            identifier = line[4:7]
            item = entry_map[identifier]
            story.append(KeepTogether([Paragraph(escape(identifier+' — '+item['question']), heading),
                Paragraph(escape(item['answer']), body), Paragraph(escape('Fonte: '+item['reference']), ref), Spacer(1, 6)]))
    references = text.split('## Referências consultadas', 1)[1].strip()
    story.append(Paragraph('Referências e limites da edição', heading))
    for paragraph in references.split('\n\n'):
        story.append(Paragraph(escape(paragraph).replace('\n', '<br/>'), ref))
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    def footer(canvas, doc):
        canvas.setFont('AstroFAQ', 8)
        canvas.setFillColor(colors.HexColor('#5c536f'))
        canvas.drawString(42, 25, 'Astro • FAQ funcional v2.0 • 01/11/2026')
        canvas.drawRightString(A4[0]-42, 25, str(doc.page))
    SimpleDocTemplate(str(output), pagesize=A4, leftMargin=42, rightMargin=42, topMargin=36, bottomMargin=44,
                      title='ASTRO — FAQ funcional v2.0', author='Astro').build(story, onFirstPage=footer, onLaterPages=footer)
    pages = [p.extract_text() or '' for p in PdfReader(output).pages]
    fingerprint = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    records = []
    for item in entries:
        found = [i for i, page in enumerate(pages) if re.search(r'\b'+item['faq_id']+r'\b', page)]
        if len(found) != 1:
            raise ValueError('Não foi possível comprovar uma página única por pergunta.')
        normalize = lambda s: re.sub(r'\s+', '', s)
        if normalize(item['answer']) not in normalize(pages[found[0]]):
            raise ValueError('Conteúdo do PDF difere do Markdown.')
        records.append({'page_content': item['page_content'], 'source': Path(output).name,
                        'page_number': found[0], 'modelo_embedding': EMBEDDING_MODEL,
                        'faq_id': item['faq_id'], 'faq_version': '2.0', 'source_sha256': fingerprint,
                        'knowledge_revision': 'f051f16014f1b85d7d9bc7d02f37e347dacfd097'})
    return records, len(pages)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--pdf', type=Path, default=PDF)
    parser.add_argument('--records', type=Path, default=Path('tmp/faq-update/records.json'))
    args = parser.parse_args()
    records, pages = build(args.source, args.pdf)
    args.records.parent.mkdir(parents=True, exist_ok=True)
    args.records.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'questions': len(records), 'pages': pages, 'max_chunk_characters': max(len(x['page_content']) for x in records),
                      'input_tokens_byte_upper_bound': sum(len(x['page_content'].encode()) for x in records), 'pdf': str(args.pdf)}))


if __name__ == '__main__':
    main()
