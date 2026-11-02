"""Regression for empty RH status lists leaking into OperationalDecision."""
import asyncio
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_chat import chat_client
from app.api import auth
from app.core.security import CurrentUser
from app.modules.chat.graph import _grafico_da_listagem
from app.modules.rh.tools import BuscarOutrosUsuariosArgs
from app.modules.operational.tools import OperationalDecision


def prepare_session(app, saved):
    app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid='user-a',role='GESTOR_WORKSPACE')
    repo=app.state.chat_service.repository
    sid=str(uuid4())
    async def seed():
        await repo.ensure(sid,'user-a')
        repo.docs[sid].update(ultima_rota='rh',consulta_rh=saved,
            mensagens=[{'role':'human','content':'Liste 100 usuários da minha equipe em uma tabela.'},
                       {'role':'assistant','content':'100 de 141 disponíveis.'}])
    asyncio.run(seed())
    return sid


@pytest.mark.parametrize('action,question', [
    ('consultar_indicadores_equipe','Faca um grafico de barras por cargo com base nessa listagem, considerando toda a minha equipe autorizada.'),
    ('consultar_grafico_pendencias_nr','Mostre pendencias por NR em barras com base nesses dados da lista.'),
    ('consultar_grafico_vencimentos','Mostre vencimentos em barras com base nesses dados da lista.')])
@pytest.mark.parametrize('lists', [{}, {'tipos':['COLABORADOR']}, {'status':['ATIVO']}])
def test_model_router_adapts_valid_saved_empty_lists_without_losing_scope(chat_client,monkeypatch,action,question,lists):
    from app.modules.operational.tools import REGISTRY
    client,model,app=chat_client
    # Real RH serialization: omitted status/tipos become [], not None.
    saved=BuscarOutrosUsuariosArgs(limite=100,nome='Ana',cargo='Analista',**lists).model_dump(mode='json')
    sid=prepare_session(app,saved)
    model.replies['roteador']=json.dumps({'acao':action,'filtros':{'limite':20}})
    calls=[]
    class Tool:
        async def ainvoke(self,args,config):
            calls.append((args,config['configurable']))
            return {'status':'ok','titulo':'Agregado autorizado','colunas':[{'chave':'categoria','titulo':'Categoria'},{'chave':'quantidade','titulo':'Quantidade'}],
                'linhas':[{'categoria':'Analista','quantidade':141}],'total':1,'pagina':1,'total_paginas':1,
                'grafico':[{'categoria':'Analista','valor':141}],'tipo_grafico':'grafico_barras',
                'unidade_grafico':'pessoas','fonte':'Base autorizada simulada'}
    monkeypatch.setitem(REGISTRY,action,Tool())
    body_response=client.post('/chat/messages',json={'message':question,'session_id':sid})
    assert body_response.status_code==200
    body=body_response.json()
    assert len(calls)==1
    args,context=calls[0]
    assert args['status']==(saved['status'] or None) and args['tipos']==(saved['tipos'] or None)
    assert args['nome']=='Ana' and args['cargo']=='Analista'
    assert args['limite']==20 and args['pagina']==1
    assert context['usuario_atual']['uid']=='user-a'
    assert context['referencia_listagem_rh']==(action=='consultar_indicadores_equipe')
    chart=next(b for b in body['blocos'] if b['tipo']=='grafico_barras')
    assert chart['dados']==[{'categoria':'Analista','valor':141}]
    assert 'juiz' in body['agentes_chamados'] and 'guardrail_saida' in body['agentes_chamados']
    history=client.get(f'/sessions/{sid}/messages').json()
    assert history['mensagens'][-1]['blocos']==body['blocos']


def test_current_explicit_filters_are_not_replaced_by_old_empty_lists(chat_client,monkeypatch):
    from app.modules.operational.tools import REGISTRY
    client,model,app=chat_client
    sid=prepare_session(app,BuscarOutrosUsuariosArgs(limite=100).model_dump(mode='json'))
    model.replies['roteador']=json.dumps({'acao':'consultar_indicadores_equipe',
        'filtros':{'status':['DESATIVADO'],'tipos':['COLABORADOR'],'cargo':'Tecnico'}})
    calls=[]
    class Tool:
        async def ainvoke(self,args,config):
            calls.append(args)
            return {'status':'sem_dados','mensagem':'Sem registros com esses critérios.'}
    monkeypatch.setitem(REGISTRY,'consultar_indicadores_equipe',Tool())
    response=client.post('/chat/messages',json={'session_id':sid,'message':'Mostre barras com base nesses dados, somente colaboradores desativados do cargo Tecnico.'})
    assert response.status_code==200
    assert calls[0]['status']==['DESATIVADO'] and calls[0]['tipos']==['COLABORADOR'] and calls[0]['cargo']=='Tecnico'


@pytest.mark.parametrize('patch',[{'status':['INEXISTENTE']},{'tipos':['ADMIN']},{'nome':'x'*121},{'pagina':2}])
def test_invalid_inherited_filters_clarify_without_query_or_scope_expansion(chat_client,monkeypatch,patch):
    from app.modules.operational.tools import REGISTRY
    client,model,app=chat_client
    saved={**BuscarOutrosUsuariosArgs(limite=100).model_dump(mode='json'),**patch}
    sid=prepare_session(app,saved)
    model.replies['roteador']=json.dumps({'acao':'consultar_indicadores_equipe','filtros':{}})
    calls=[]
    class Tool:
        async def ainvoke(self,*args,**kwargs):
            calls.append(args)
            raise AssertionError('Invalid saved filters must not query')
    monkeypatch.setitem(REGISTRY,'consultar_indicadores_equipe',Tool())
    response=client.post('/chat/messages',json={'session_id':sid,'message':'Mostre barras por cargo com base nesses dados da lista anterior.'})
    assert response.status_code==200 and not calls
    assert 'Consulte a lista de pessoas novamente' in str(response.json()['blocos'])


def test_fast_chart_path_uses_same_empty_list_translation_and_strict_tool_contract():
    previous=BuscarOutrosUsuariosArgs(limite=100).model_dump(mode='json')
    result=_grafico_da_listagem({'mensagem':'Faça um gráfico de barras por cargo com base nessa listagem.', 'consulta_rh':previous})
    assert isinstance(result,OperationalDecision) and result.referencia_listagem_rh
    assert result.filtros.status is None and result.filtros.tipos is None
    # Empty model-supplied filters remain invalid: only trusted RH semantics are adapted.
    with pytest.raises(ValidationError) as error:
        OperationalDecision(acao='consultar_indicadores_equipe',filtros={'status':[]})
    assert error.value.errors()[0]['loc']==('status',)
    assert error.value.errors()[0]['type']=='too_short'
    with pytest.raises(ValidationError):
        OperationalDecision(acao='consultar_indicadores_equipe',filtros={'tipos':[]})


def test_empty_saved_profile_is_not_replaced_by_an_inferred_role(chat_client,monkeypatch):
    from app.modules.operational.tools import REGISTRY
    client,model,app=chat_client
    sid=prepare_session(app,BuscarOutrosUsuariosArgs(limite=100).model_dump(mode='json'))
    model.replies['roteador']=json.dumps({'acao':'consultar_indicadores_equipe','filtros':{}})
    calls=[]
    class Tool:
        async def ainvoke(self,args,config):
            calls.append(args)
            return {'status':'sem_dados','mensagem':'Sem registros.'}
    monkeypatch.setitem(REGISTRY,'consultar_indicadores_equipe',Tool())
    response=client.post('/chat/messages',json={'session_id':sid,'message':'Mostre barras por cargo com base nesses dados dos funcionários.'})
    assert response.status_code==200
    assert calls[0]['tipos'] is None and calls[0]['status'] is None
