from pathlib import Path

import pytest
from pypdf import PdfReader

from app.scripts.build_product_faq import SOURCE, build, questions


def test_reviewed_product_faq_preserves_every_answer_and_real_page(tmp_path):
    pdf = tmp_path / 'faq.pdf'
    records, pages = build(SOURCE, pdf)
    assert len(records) == 46 and pages == len(PdfReader(pdf).pages)
    assert len({r['faq_id'] for r in records}) == len(records)
    for record in records:
        assert record['source'] == 'faq.pdf'
        assert 0 <= record['page_number'] < pages
        assert record['modelo_embedding'] == 'openai/text-embedding-3-small'
        assert len(record['page_content']) <= 1800
        assert record['faq_id'] in PdfReader(pdf).pages[record['page_number']].extract_text()
        assert record['knowledge_revision'] == 'f051f16014f1b85d7d9bc7d02f37e347dacfd097'
    support = next(r for r in records if r['faq_id'] == 'F46')
    assert 'app.4str0@gmail.com' in support['page_content']
    assert '07h às 18h' in support['page_content']
    assert all('não há compartilhamento de informações com terceiros' not in r['page_content'].lower() for r in records)


@pytest.mark.parametrize('source', [
    '# FAQ vazio',
    '### F01 — Pergunta\nResposta sem fonte.',
    '### F01 — Pergunta\n\nFonte: K1.',
    '### F01 — Pergunta\nResposta.\nFonte: K1.\n### F01 — Repetida\nResposta.\nFonte: K1.',
    '### F01 — Pergunta\n' + 'x'*1801 + '\nFonte: K1.',
])
def test_invalid_faq_cannot_generate_indexable_records(source):
    with pytest.raises(ValueError):
        questions(source)
