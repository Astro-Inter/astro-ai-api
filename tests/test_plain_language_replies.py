"""Presentation changes preserve evidence while removing technical field dumps."""
import copy
import pytest
from app.modules.chat.subgraphs import (
    _formatar_situacao_nrs, _formatar_nrs_obrigatorias, _formatar_treinamentos,
    _referencias_eventos, _formatar_nrs, _data_legivel,
)


def nr(situation='PENDENTE',action='CONCLUIR_PENDENCIA',validity=None):
    return {'numero':1,'titulo':'Disposições gerais','situacao':situation,'acao_necessaria':action,
            'data_validade':validity,'data_inicio_pendencia':'2026-10-26T03:00:00',
            'data_termino_pendencia':'2026-10-27T03:00:00'}


def test_nr_pending_is_a_paragraph_not_a_table_and_does_not_mutate_data():
    result={'status':'ok','cargo':'Técnico','unidade':'Matriz','nrs':[nr(),{**nr(),'numero':5}]}
    original=copy.deepcopy(result)
    text=_formatar_situacao_nrs(result)
    assert 'NR-1 — Disposições gerais: tem uma atividade pendente. Conclua a atividade pendente.' in text
    assert 'NR-5' in text and '\n\n' in text
    assert '|' not in text and 'prevista' not in text and '2026-10-26' not in text
    assert text.count('Não é possível confirmar')==1 and 'registrada' not in text
    assert result==original


@pytest.mark.parametrize('situation,action,expected',[
    ('VIGENTE','NENHUMA','está em dia'),
    ('RENOVACAO_NECESSARIA','RENOVAR','precisa ser renovada'),
    ('REALIZACAO_NECESSARIA','REALIZAR','precisa ser realizada'),
    ('PENDENTE','CONCLUIR_PENDENCIA','tem uma atividade pendente'),
])
def test_nr_situation_and_relevant_expiry_are_not_lost(situation,action,expected):
    result={'status':'ok','cargo':'Técnico','unidade':'Matriz','nrs':[nr(situation,action,'2027-09-12')]}
    text=_formatar_situacao_nrs(result)
    assert expected in text and 'Validade: 12/09/2027.' in text and '|' not in text


def test_absent_or_invalid_validity_does_not_assert_validity():
    for value in [None,'not-a-date']:
        text=_formatar_situacao_nrs({'status':'ok','cargo':'Técnico','unidade':'Matriz','nrs':[nr('VIGENTE','NENHUMA',value)]})
        assert 'está em dia' not in text and 'sem validade confirmada' in text
        assert 'Não é possível confirmar' in text


def test_other_person_and_empty_cases_stay_clear_without_false_all_clear():
    text=_formatar_situacao_nrs({'status':'ok','consulta_terceiro':True,'usuario':'Pessoa exemplo','cargo':'Técnico','unidade':'Matriz','nrs':[nr()]})
    assert text.startswith('NRs de Pessoa exemplo:') and ' | ' not in text
    empty=_formatar_situacao_nrs({'status':'ok','cargo':'Técnico','unidade':'Matriz','nrs':[]})
    assert 'vinculada' in empty and 'está em dia' not in empty
    assert _formatar_situacao_nrs({'status':'indisponivel','mensagem':'Consulta indisponível.'})=='Consulta indisponível.'


def test_other_pipe_based_lists_keep_meaning_and_selection_references():
    training={'evento_id':12,'titulo':'Treinamento exemplo','turma':'A','data_inicio':'2026-09-20T08:00:00',
        'data_termino':'2026-09-20T12:00:00','status_participacao':'REJEITADO','status_evento':'CANCELADO',
        'modo_conclusao':'GESTOR','evidencia_obrigatoria':True,'motivo_rejeicao':'Arquivo ilegível.',
        'nr':{'numero':10,'titulo':'Eletricidade'}}
    text=_formatar_treinamentos({'status':'ok','treinamentos':[training],'pagina':1,'total_paginas':1})
    assert ' | ' not in text and '20/09/2026 às 08:00' in text
    assert 'participação está rejeitada' in text and 'evento está cancelado' in text
    assert 'evidência para comprovar' in text and 'Arquivo ilegível' in text
    assert 'referência do evento: 12' in text
    assert 'evento_id' not in text
    refs=_referencias_eventos({'status':'ok','eventos':[training]})
    assert ' | ' not in refs and 'referência do evento: 12' in refs
    assert 'evento_id' not in refs
    mandatory=_formatar_nrs_obrigatorias({'status':'ok','cargo':'Técnico','unidade':'Matriz',
        'nrs':[{'numero':10,'titulo':'Eletricidade','tempo_reciclagem_meses':24}]})
    assert ' | ' not in mandatory and 'NR-10' in mandatory
    assert 'cada 24 meses' not in mandatory and 'não confirma conclusão ou validade' in mandatory
    catalog=_formatar_nrs({'status':'ok','modo':'listar','nrs':[{'numero':10,'nome':'Eletricidade','situacao':'VIGENTE'}]})
    assert ' | ' not in catalog and 'Situação da norma: vigente' in catalog


def test_calendar_formatting_does_not_invent_timezone_conversion():
    assert _data_legivel('2026-10-26T03:00:00Z',horario=True)=='26/10/2026 às 03:00'
    assert _data_legivel('2026-10-26')=='26/10/2026'
    assert _data_legivel('2026-10-26',horario=True)=='26/10/2026'
    assert _data_legivel('invalid') is None
