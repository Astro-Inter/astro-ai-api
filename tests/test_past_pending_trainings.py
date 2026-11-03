import copy
import pytest
from app.modules.chat.subgraphs import _formatar_treinamentos

@pytest.mark.parametrize('end,past',[('2026-10-20T12:00:00',True),('2026-11-02T12:00:00',False),('2026-12-20T12:00:00',False),(None,False),('invalid',False)])
def test_pending_and_time_period_are_independent(end,past):
    data={'status':'ok','pagina':1,'total_paginas':1,'treinamentos':[{'titulo':'Curso exemplo','evento_id':12,'turma':'A',
        'data_inicio':'2026-10-20T08:00:00','data_termino':end,'status_participacao':'PENDENTE','status_evento':'ATIVO',
        'modo_conclusao':'GESTOR','evidencia_obrigatoria':True}]}
    original=copy.deepcopy(data)
    text=_formatar_treinamentos(data,'2026-11-02T01:00:00-03:00')
    assert ('O período informado já terminou.' in text)==past
    assert 'Sua participação está pendente.' in text
    assert 'Começa em' not in text and 'Termina em' not in text
    assert 'referência do evento: 12' in text
    assert 'evidência para comprovar' in text and data==original


def test_homologation_label_is_explicit_and_pending_not_duplicated():
    data={'status':'ok','pagina':1,'total_paginas':1,'treinamentos':[{
        'titulo':'HOMOLOGAÇÃO CHAT — teste','turma':'HOMOLOGAÇÃO — Passada',
        'data_inicio':'2026-10-20T08:00:00','data_termino':'2026-10-20T09:00:00',
        'status_participacao':'PENDENTE','status_evento':'ATIVO',
        'modo_conclusao':'COLABORADOR','evidencia_obrigatoria':False}]}
    text=_formatar_treinamentos(data,'2026-11-02')
    assert 'não representam capacitação ou certificação real' in text
    assert text.count('Sua participação está pendente.')==1
    assert 'A participação continua pendente.' not in text
