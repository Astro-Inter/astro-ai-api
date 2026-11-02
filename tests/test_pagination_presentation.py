"""Transport metadata must not become redundant prose or erase partial-result warnings."""
import pytest
from app.modules.chat.session_context import message_context, block_context
from app.modules.chat.subgraphs import _formatar_usuarios, _formatar_nrs


def test_old_single_page_footer_is_removed_only_from_private_assistant_context():
    original = 'Dados consultados. Página 1 de 1. Total: 8. Fonte: manual.pdf, página 1.'
    message = {'role':'assistant', 'content':original}
    projected = message_context(message)
    assert 'Página 1 de 1' not in projected
    assert 'Total: 8' in projected and 'manual.pdf, página 1.' in projected
    assert message_context({'role':'assistant','content':'Página 1 de 10. Total: 200.'}) == 'Página 1 de 10. Total: 200.'
    assert message['content'] == original
    assert message_context({'role':'user', 'content':original}) == original


@pytest.mark.parametrize('pagination', [{'pagina':1,'total_paginas':1},
    {'paginacao':'local','pagina':1,'total_paginas':1,'retornada':100,'total':141,'tem_mais':True}])
def test_context_omits_transport_pages_but_keeps_counts(pagination):
    original = {'tipo':'tabela','titulo':'Equipe','linhas':[],**pagination}
    projected = block_context([original])[0]
    assert 'pagina' not in projected and 'total_paginas' not in projected
    assert original['pagina'] == 1
    if 'total' in pagination:
        assert projected['total'] == 141 and projected['retornada'] == 100 and projected['tem_mais']
    # A real server subset retains its metadata so context cannot imply completeness.
    assert block_context([{'tipo':'tabela','pagina':2,'total_paginas':3,'linhas':[]}])[0]['pagina'] == 2


@pytest.mark.parametrize('total', [1,3])
def test_nr_count_never_claims_partial_set_is_complete(total):
    text = _formatar_nrs({'status':'ok','modo':'listar','nrs':[{'numero':10,'nome':'Norma sintética'}],
                         'paginacao':{'pagina':1,'total_paginas':total,'total':total}})
    assert f'Retornadas 1 de {total}' in text and 'Página' not in text
    assert ('parcial' in text) == (total > 1)


def test_local_users_preserve_cut_warning_without_page_instructions():
    text = _formatar_usuarios({'status':'ok','retornada':100,'total':141,'solicitada':100,
                               'limitado':False,'tem_mais':True,'pagina':1,'total_paginas':1})
    assert '100' in text and '141' in text and 'parcial' in text and 'página' not in text.lower()
