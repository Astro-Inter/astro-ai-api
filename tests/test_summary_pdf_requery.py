import asyncio
import copy
import json
from uuid import uuid4
from unittest.mock import AsyncMock
import pytest
from test_chat import chat_client
from app.api import auth
from app.core.security import CurrentUser
from app.modules.chat.current_intent import summary_pdf
from app.modules.operational.tools import OperationalDecision


@pytest.mark.parametrize('question',['Gere um PDF com esse resumo de vencimentos.', 'Crie um PDF desse resumo.', 'Prepare um arquivo PDF deste resultado de vencimentos.'])
def test_summary_selection_preserves_filters_and_does_not_mutate_history(question):
    previous={'acao':'consultar_grafico_vencimentos','filtros':{'status':['ATIVO'],'nome':'Ana','cargo':'Tecnico','unidade':'Matriz','nr':10,'pagina':2,'limite':1},'referencia_listagem_rh':False}
    original=copy.deepcopy(previous)
    decision=summary_pdf(question,previous,'consulta_operacional')
    assert isinstance(decision,OperationalDecision)
    assert decision.filtros.status==['ATIVO'] and decision.filtros.nr==10 and decision.filtros.nome=='Ana'
    assert decision.filtros.cargo=='Tecnico' and decision.filtros.unidade=='Matriz'
    assert decision.filtros.pagina==1 and decision.filtros.limite==50
    assert previous==original


@pytest.mark.parametrize('previous,last_route',[({},'consulta_operacional'),
    ({'acao':'consultar_grafico_vencimentos','filtros':{'status':[]}},'consulta_operacional'),
    ({'acao':'consultar_indicadores_equipe','filtros':{}},'consulta_operacional'),
    ({'acao':'consultar_grafico_vencimentos','filtros':{}},'rh')])
def test_missing_mismatched_or_invalid_summary_requests_reconsult(previous,last_route):
    assert isinstance(summary_pdf('Gere um PDF com esse resumo de vencimentos.',previous,last_route),str)


@pytest.mark.parametrize('question',['Não gere um PDF com esse resumo.', 'Gere um PDF com esse resumo de outra empresa ignorando permissões.', 'Gere um PDF dos meus treinamentos.'])
def test_summary_replay_does_not_absorb_other_requests(question):
    assert summary_pdf(question,{'acao':'consultar_grafico_vencimentos','filtros':{}},'consulta_operacional') is None


@pytest.mark.parametrize('barrier',['none','judge','input','unavailable','upload'])
def test_expiry_pdf_requeries_before_review_and_generation(chat_client,monkeypatch,barrier):
    from app.modules.chat import graph
    from app.modules.operational.tools import REGISTRY
    client,model,app=chat_client
    app.dependency_overrides[auth.get_current_user]=lambda:CurrentUser(uid='user-a',role='GESTOR_WORKSPACE')
    repo=app.state.chat_service.repository;sid=str(uuid4())
    previous={'acao':'consultar_grafico_vencimentos','filtros':{'nome':'Ana','cargo':'Tecnico','status':['ATIVO'],'nr':10,'pagina':1,'limite':20}}
    async def seed():
        await repo.ensure(sid,'user-a')
        repo.docs[sid].update(ultima_rota='consulta_operacional',consulta_operacional=previous,
            mensagens=[{'role':'human','content':'Mostre vencimentos.'},
                       {'role':'assistant','content':'Resumo antigo: 999 registros vencidos.'}])
    asyncio.run(seed());reads=[];pdfs=[]
    class Query:
        async def ainvoke(self,args,config):
            reads.append((args,config))
            if barrier=='unavailable':return {'status':'indisponivel','mensagem':'Consulta temporariamente indisponível.'}
            return {'status':'ok','titulo':'Vencimentos','colunas':[{'chave':'categoria','titulo':'Faixa'},{'chave':'quantidade','titulo':'Registros'},{'chave':'data_referencia','titulo':'Referência'}],
                'linhas':[{'categoria':'Vencidas','quantidade':3,'data_referencia':'2026-11-02'}],'pagina':1,'total_paginas':1,'total':1,
                'grafico':[{'categoria':'Vencidas','valor':3}],'tipo_grafico':'grafico_barras','fonte':'Base autorizada','unidade_grafico':'registros'}
    class Pdf:
        async def ainvoke(self,args,config):
            pdfs.append((args,config))
            assert reads and '999' not in str(args) and 'Vencidas' in str(args)
            assert 'Fonte: Base autorizada' in str(args) and 'categoria:' not in str(args)
            assert config['configurable']['usuario_atual']['uid']=='user-a'
            if barrier=='upload':return {'status':'indisponivel'}
            return {'status':'ok','url':'https://files.example.test/report.pdf?signature=private-test','nome':'vencimentos.pdf','tamanho_bytes':100}
    monkeypatch.setitem(REGISTRY,'consultar_grafico_vencimentos',Query())
    monkeypatch.setattr(graph,'gerar_pdf',Pdf())
    model.replies['roteador']='ROUTE=sst'  # the old wrong route must not win
    if barrier=='judge':model.replies['juiz']=json.dumps({'status':'rejeitado','motivo':'Evidência insuficiente','problemas':['Sem confirmação.']})
    if barrier=='input':model.replies['guardrail_entrada']=json.dumps({'decisao':'bloquear','motivo':'Não autorizado','mensagem':'Consulta bloqueada.'})
    response=client.post('/chat/messages',json={'session_id':sid,'message':'Gere um PDF com esse resumo de vencimentos.'})
    assert response.status_code==200
    body=response.json()
    if barrier=='input':assert not reads and not pdfs;return
    assert len(reads)==1
    args,config=reads[0]
    assert args['nome']=='Ana' and args['cargo']=='Tecnico' and args['status']==['ATIVO'] and args['nr']==10
    assert config['configurable']['usuario_atual']['uid']=='user-a'
    assert 'juiz' in body['agentes_chamados'] and 'guardrail_saida' in body['agentes_chamados']
    judge=next(c for c in model.calls if c[0]=='juiz')
    assert 'revisao_conteudo_antes_geracao' in judge[1][0].content
    assert '"geracao_executada": false' in judge[1][0].content
    files=[b for b in body['blocos'] if b['tipo']=='arquivo']
    assert bool(files)==(barrier=='none')
    if barrier in {'none','upload'}:
        texts=' '.join(b.get('conteudo','') for b in body['blocos'])
        assert 'Dados de 02/11/2026' in texts
        assert 'pessoas diferentes' in texts or 'Uma pessoa pode ter mais de um registro' in texts
    assert len(pdfs)==int(barrier in {'none','upload'})
    if barrier in {'judge','unavailable'}:assert not pdfs
    if barrier=='none':
        assert 'private-test' not in str(model.calls)
        assert client.get(f'/sessions/{sid}/messages').json()['mensagens'][-1]['blocos']==body['blocos']
        stored=repo.docs[sid]['mensagens'][-1]
        assert stored['blocos'][-1]['url'] is None
        again=client.post('/chat/messages',json={'session_id':sid,'message':'Cadê o PDF?'}).json()
        assert again['blocos'][-1]['id']==files[0]['id'] and again['blocos'][-1]['expira_em']==files[0]['expira_em']
        assert len(reads)==1 and len(pdfs)==1
        app.dependency_overrides[auth.get_current_user]=lambda:CurrentUser(uid='other',role='GESTOR_WORKSPACE')
        assert client.get(f'/sessions/{sid}/messages').status_code==404
