import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from bson import BSON

from test_chat import chat_client, no_remote_tracing, texto_resposta
from app.api import auth
from app.core.security import CurrentUser
from app.modules.chat.errors import ChatError


def manager(application):
    application.dependency_overrides[auth.get_current_user]=lambda:CurrentUser(uid="viewer",role="GESTOR_WORKSPACE")


@pytest.mark.parametrize("review",["aprovado","revisar","rejeitado","unavailable"])
@pytest.mark.parametrize("empty",[False,True])
def test_analytic_review_never_claims_components_without_delivery(chat_client,monkeypatch,review,empty):
    from app.modules.operational import insights
    from test_analytic_visual_data import fake_db
    client,model,app=chat_client
    manager(app)
    records=[] if empty else [{"nr":10,"aplicaveis":12,"vigentes":5,"em_analise":2,"agendados":3,"sem_encaminhamento":2}]
    fake_db(monkeypatch,"consultar_indicadores_cobertura",records,(0,0,0,0,0,0,0) if empty else (12,8,5,5,2,3,2),[] if empty else [("NR-10",2)])
    if review=="unavailable":
        original=model.complete
        async def complete(agent,*a,**kw):
            if agent=="juiz":raise ChatError(503,"Revisor indisponivel")
            return await original(agent,*a,**kw)
        monkeypatch.setattr(model,"complete",complete)
    else:
        model.replies["juiz"]=json.dumps({"status":review,"motivo":"Revisão simulada","problemas":[] if review=="aprovado" else ["Sem validação"]})
    response=client.post('/chat/messages',json={"message":"Mostre indicadores de cobertura da minha equipe."})
    if review=="unavailable":
        assert response.status_code==503
        return
    assert response.status_code==200
    body=response.json()
    if review!="aprovado":
        assert all(b['tipo']=='texto' for b in body['blocos'])
        assert "Retornados" not in texto_resposta(body)
        assert not app.state.chat_service.repository.docs[body['session_id']]['mensagens']
    else:
        assert [b['tipo'] for b in body['blocos']]==(['indicadores','contexto_dados'] if empty else ['indicadores','tabela','grafico_barras','contexto_dados'])
        history=client.get(f"/sessions/{body['session_id']}/messages").json()
        assert history['mensagens'][-1]['blocos']==body['blocos']


def test_current_explicit_intent_changes_within_one_session(chat_client,monkeypatch):
    from app.modules.operational import insights
    from test_analytic_visual_data import fake_db,matrix_rows
    client,model,app=chat_client
    manager(app)
    sid=None
    # A stale model suggestion must never replace these explicit current reads.
    model.replies['roteador']=json.dumps({'acao':'consultar_indicadores_cobertura','filtros':{}})
    cases=[('consultar_indicadores_cobertura','Mostre indicadores de cobertura da minha equipe.',
            [{'nr':10,'aplicaveis':1,'vigentes':1,'em_analise':0,'agendados':0,'sem_encaminhamento':0}],[("NR-10",0)],'tabela'),
           ('consultar_matriz_conformidade_equipe','Mostre 100 registros da matriz de conformidade da minha equipe.',matrix_rows(2),None,'matriz_conformidade'),
           ('comparar_conformidade_unidades','Compare a conformidade entre as unidades.',
            [{'unidade_id':1,'unidade':'Unidade','pessoas':1,'aplicaveis':1,'vigentes':1,'percentual':100.0}],[("Unidade (#1)",1)],'tabela')]
    for action,question,rows,groups,kind in cases:
        fake_db(monkeypatch,action,rows,(1,1,1,0,0,0,0),groups)
        r=client.post('/chat/messages',json={'message':question,**({'session_id':sid} if sid else {})})
        assert r.status_code==200
        body=r.json();sid=body['session_id']
        assert action in body['agentes_chamados']
        assert kind in [b['tipo'] for b in body['blocos']]
        assert body['blocos'][-1]['conjunto']['total']==len(rows)
        assert client.get(f'/sessions/{sid}/messages').json()['mensagens'][-1]['blocos']==body['blocos']


def test_no_conflicts_answers_question_without_promising_graph(chat_client,monkeypatch):
    from test_analytic_visual_data import fake_db
    client,model,app=chat_client
    manager(app)
    fake_db(monkeypatch,'consultar_conflitos_agenda',[],(0,0))
    body=client.post('/chat/messages',json={'message':'Tenho treinamentos no mesmo horário?'}).json()
    assert [b['tipo'] for b in body['blocos']]==['indicadores','contexto_dados']
    review=next(c for c in model.calls if c[0]=='juiz')
    candidate=json.loads(review[1][-1].content.split('\n',1)[1])['resposta_candidata']
    assert 'Não encontrei treinamentos com horários sobrepostos' in candidate
    assert 'gráfico' not in candidate


def test_pdf_file_block_roundtrip_expiration_and_ownership(chat_client,monkeypatch):
    from app.modules.chat import graph
    from app.infrastructure.database.sessions import utc_now
    client,model,app=chat_client
    model.route='faq'
    class Pdf:
        async def ainvoke(self,*a,**kw):
            return {'status':'ok','url':'https://files.example/report.pdf?signature=private-test','nome':'relatorio.pdf','tamanho_bytes':1234}
    monkeypatch.setattr(graph,'gerar_pdf',Pdf())
    response=client.post('/chat/messages',json={'message':'Gere um PDF sobre o Astro.'})
    assert response.status_code==200
    body=response.json();file=body['blocos'][-1]
    assert file['tipo']=='arquivo' and file['mime_type']=='application/pdf'
    assert file['nome']=='relatorio.pdf' and file['tamanho_bytes']==1234
    assert file['url'].endswith('private-test') and file['estado']=='disponivel'
    sid=body['session_id'];repo=app.state.chat_service.repository
    stored=repo.docs[sid]['mensagens'][-1]
    assert stored['blocos'][-1]['url'] is None
    assert 'private-test' not in stored['content']+str(model.calls)
    BSON.encode(stored)
    assert client.get(f'/sessions/{sid}/messages').json()['mensagens'][-1]['blocos']==body['blocos']
    # A link reminder preserves the original expiry, never extends it.
    reminder=client.post('/chat/messages',json={'session_id':sid,'message':'Cadê o PDF?'}).json()
    assert reminder['blocos'][-1]['expira_em']==file['expira_em']
    assert reminder['blocos'][-1]['url']==file['url']
    for delivery in repo.docs[sid]['pdf_entregas'].values():
        delivery['expira_em']=utc_now()-timedelta(seconds=1)
    for message in repo.docs[sid]['mensagens']:
        if message.get('pdf_referencia'):
            message['blocos'][-1]['expira_em']=(utc_now()-timedelta(seconds=1)).isoformat()
    history=client.get(f'/sessions/{sid}/messages').json()
    assert history['mensagens'][-1]['blocos'][-1]['estado']=='expirado'
    assert history['mensagens'][-1]['blocos'][-1]['url'] is None
    app.dependency_overrides[auth.get_current_user]=lambda:CurrentUser(uid='other',role='GESTOR_WORKSPACE')
    assert client.get(f'/sessions/{sid}/messages').status_code==404


def test_personal_lines_zero_values_include_visual_plan(chat_client,monkeypatch):
    from app.modules.chat import subgraphs
    client,model,app=chat_client
    class Tool:
        async def ainvoke(self,*a,**kw):
            return {'status':'ok','categorias':{f'2026-{m:02d}':0 for m in range(1,13)},'fonte':'Conclusões pessoais'}
    monkeypatch.setitem(subgraphs.AGENDA_TOOLS,'consultar_evolucao_treinamentos',Tool())
    body=client.post('/chat/messages',json={'message':'Mostre um gráfico de linhas dos meus treinamentos concluídos.'}).json()
    chart=next(b for b in body['blocos'] if b['tipo']=='grafico_linhas')
    assert len(chart['dados'])==12 and all(p['valor']==0 for p in chart['dados'])
    review=next(c for c in model.calls if c[0]=='juiz')
    assert 'grafico_linhas' in review[1][-1].content and 'valores_zero_preservados' in review[1][-1].content
    assert client.get(f"/sessions/{body['session_id']}/messages").json()['mensagens'][-1]['blocos']==body['blocos']


@pytest.mark.parametrize('result_status',['ok','nao_autorizado'])
def test_listed_event_title_resolves_reference_and_java_authorizes(chat_client,monkeypatch,result_status):
    from app.modules.chat import subgraphs
    from app.modules.operational import insights
    client,model,app=chat_client
    manager(app)
    class Events:
        async def ainvoke(self,*a,**kw):
            return {'status':'ok','proximos':False,'pagina':1,'total_paginas':1,'total':1,'eventos':[
                {'evento_id':12,'titulo':'Treinamento exemplo','turma':'Turma A','data_inicio':'2026-11-01','data_termino':'2026-11-02',
                 'status_participacao':'PENDENTE','status_evento':'ATIVO','modo_conclusao':'GESTOR','evidencia_obrigatoria':False,
                 'link_externo':'http://localhost:5173/mainEventScreen'}]}
    monkeypatch.setitem(subgraphs.AGENDA_TOOLS,'consultar_eventos',Events())
    first=client.post('/chat/messages',json={'message':'Liste meus eventos e treinamentos disponíveis com a referência para selecionar um.'}).json()
    assert 'referência do evento: 12' in texto_resposta(first)
    assert 'http://localhost:5173/mainEventScreen' in texto_resposta(first)
    seen=[]
    async def api(path,params):
        seen.append(path)
        return {'status':'ok','data':[{'participantId':1,'participationStatus':'CONCLUIDO'}]} if result_status=='ok' else {'status':result_status,'mensagem':'Acesso negado.'}
    monkeypatch.setattr(insights,'read_api',api)
    body=client.post('/chat/messages',json={'session_id':first['session_id'],'message':'Faça um resumo do treinamento Treinamento exemplo.'}).json()
    assert seen==['/events/12/participants']
    if result_status=='ok':assert body['blocos'][0]['tipo']=='indicadores'
    else:assert all(b['tipo']=='texto' for b in body['blocos']) and 'negado' in texto_resposta(body)
    assert client.get(f"/sessions/{first['session_id']}/messages").json()['mensagens'][-1]['blocos']==body['blocos']


def test_ambiguous_event_selection_and_new_intent():
    from app.modules.chat.current_intent import selected_event,explicit_read
    refs=[{'evento_id':1,'titulo':'Curso'},{'evento_id':2,'titulo':'Curso'}]
    assert 'mais de um' in selected_event('Faça um resumo do treinamento Curso',refs)
    assert selected_event('Faça um resumo do evento 2',refs).filtros.evento_id==2
    assert selected_event('Mostre 100 registros da matriz de conformidade da equipe',refs,True) is None
    assert explicit_read('Mostre 100 registros da matriz de conformidade da equipe').acao=='consultar_matriz_conformidade_equipe'


def test_explicit_list_quantity_changes_from_100_to_500(chat_client,monkeypatch):
    from app.modules.chat import subgraphs
    client,model,app=chat_client
    manager(app)
    calls=[]
    class Users:
        async def ainvoke(self,args,config):
            calls.append(args['limite'])
            n=min(args['limite'],141)
            return {'status':'ok','consulta':'listar','quantidade':n,'usuarios':[{'nome':f'Pessoa {i}','email':None,'tipo':'COLABORADOR','cargo':'Analista','unidade':'Unidade'} for i in range(n)],
                    'total':141,'pagina':1,'total_paginas':1,'solicitada':args['limite'],'retornada':n,
                    'tem_mais':n<141,'limitado':False,'motivo_limite':None,'paginacao':'local'}
    monkeypatch.setitem(subgraphs.RH_TOOLS,'buscar_outros_usuarios',Users())
    sid=None
    for requested,n in [(100,100),(500,141)]:
        body=client.post('/chat/messages',json={'message':f'Mostre {requested} usuários da minha equipe em uma tabela.',**({'session_id':sid} if sid else {})}).json()
        sid=body['session_id']
        table=next(b for b in body['blocos'] if b['tipo']=='tabela')
        assert table['solicitada']==requested and table['retornada']==n
        assert table['total']==141 and not table['limitado'] and table['tem_mais']==(n<141)
    assert calls==[100,500]


def test_memory_summary_and_lookup_only_receive_public_message_text():
    from app.modules.memory.service import ConversationMemory
    from app.infrastructure.database.sessions import utc_now
    from memory_fakes import FakeSessions,FakeVectors
    async def scenario():
        repo=FakeSessions();vectors=FakeVectors();sid=str(uuid4())
        await repo.ensure(sid,'owner')
        doc,token=await repo.acquire(sid,'owner')
        secret='https://files.example/private?signature=not-for-model'
        repo.docs[sid]['mensagens']=[{'role':'assistant','content':'PDF gerado.',
                                    'private_metadata':{'url':secret},'blocos':[{'url':secret}]}]
        class Model:
            async def complete(self,agent,messages,**kwargs):
                assert secret not in str(messages)
                assert 'private_metadata' not in str(messages) and 'blocos' not in str(messages)
                return '{"resumo":"Usuário recebeu PDF."}'
        memory=ConversationMemory(repo,vectors,Model())
        current=await repo.get(sid,'owner')
        assert await memory.summarize(current,token)=='Usuário recebeu PDF.'
        repo.docs[sid].update(resumo='Usuário recebeu PDF.',status='encerrada')
        found=await memory.search('owner','another-session','')
        assert secret not in str(found)
    asyncio.run(scenario())


@pytest.mark.parametrize('changes',[
    {'nome':'../arquivo.pdf'},{'mime_type':'text/html'},{'url':'http://files.example/file'},
    {'url':'https://user:password@files.example/file'},{'tamanho_bytes':2000001},
])
def test_file_block_rejects_unsupported_metadata(changes):
    from pydantic import ValidationError
    from app.modules.chat.schemas import BlocoArquivo
    from app.infrastructure.database.sessions import utc_now
    with pytest.raises(ValidationError):
        BlocoArquivo(**{'id':uuid4(),'nome':'relatorio.pdf','expira_em':utc_now(),**changes})
