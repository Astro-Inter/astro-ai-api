import json

import pytest

from test_chat import chat_client, no_remote_tracing, texto_resposta
from app.api import auth
from app.core.security import CurrentUser
from app.modules.chat.errors import ChatError


def manager(app):
    app.dependency_overrides[auth.get_current_user]=lambda:CurrentUser(uid="viewer",role="GESTOR_WORKSPACE")


def test_coverage_reviewer_receives_actual_metrics_and_components(chat_client,monkeypatch):
    from test_analytic_visual_data import fake_db
    client,model,app=chat_client
    manager(app)
    # More applicable pairs than unique people; stages and n/d must reconcile.
    fake_db(monkeypatch,"consultar_indicadores_cobertura",
            [{"nr":10,"aplicaveis":12,"vigentes":5,"em_analise":2,"agendados":3,"sem_encaminhamento":2}],
            (12,8,5,5,2,3,2),[("NR-10",2)])
    original=model.complete
    async def reviewed(agent,messages,**kwargs):
        if agent=="juiz":
            evidence=json.loads(messages[-1].content.split('\n',1)[1])
            candidate=evidence['resposta_candidata']
            for fragment in ['8 pessoas','12 pares','5 pares vigentes','41.67%','5/12','2 em análise','3 agendados','2 sem encaminhamento']:
                assert fragment in candidate
            assert evidence['resultado']['apresentacao']['blocos']==['indicadores','tabela','grafico_barras','contexto_dados']
        return await original(agent,messages,**kwargs)
    monkeypatch.setattr(model,'complete',reviewed)
    response=client.post('/chat/messages',json={'message':'Mostre indicadores de cobertura da minha equipe.'})
    assert response.status_code==200
    body=response.json()
    assert [b['tipo'] for b in body['blocos']]==['indicadores','tabela','grafico_barras','contexto_dados']
    assert client.get(f"/sessions/{body['session_id']}/messages").json()['mensagens'][-1]['blocos']==body['blocos']


def test_inconsistent_coverage_counts_are_not_certified():
    from app.modules.operational.visual_data import items_from_stats
    with pytest.raises(ValueError):
        items_from_stats('consultar_indicadores_cobertura',(12,8,5,5,2,3,3))


@pytest.mark.parametrize('empty',[False,True])
def test_mobile_event_reference_phrase_uses_all_assigned_events(chat_client,monkeypatch,empty):
    from app.modules.chat import subgraphs
    from app.modules.operational import insights, tools
    client,model,app=chat_client
    manager(app)
    calls=[]
    class Events:
        async def ainvoke(self,args,config):
            calls.append(args)
            return {'status':'sem_dados' if empty else 'ok','proximos':args['proximos'],
                    'total':0 if empty else 1,'pagina':1,'total_paginas':1,'eventos':[] if empty else [
                        {'evento_id':12,'titulo':'Curso exemplo','turma':'Turma A','data_inicio':'2026-10-01','data_termino':'2026-10-02',
                         'status_participacao':'CONCLUIDO','status_evento':'ATIVO','modo_conclusao':'GESTOR','evidencia_obrigatoria':False}]}
    monkeypatch.setitem(subgraphs.AGENDA_TOOLS,'consultar_eventos',Events())
    model.replies['roteador']='Faltam informações.'
    body=client.post('/chat/messages',json={'message':'Liste meus eventos com a referencia evento_id para eu selecionar um resumo.'}).json()
    assert calls and calls[0]['proximos'] is False
    assert client.get(f"/sessions/{body['session_id']}/messages").json()['mensagens'][-1]['blocos']==body['blocos']
    if empty:
        assert texto_resposta(body) == 'Não encontrei eventos atribuídos a você no Astro.'
        assert 'evento_id' not in texto_resposta(body)
        assert not app.state.chat_service.repository.docs[body['session_id']]['eventos_referencias']
    else:
        assert 'referência do evento: 12' in texto_resposta(body)
        seen=[]
        async def api(path,params):
            seen.append(path)
            if not path.endswith('/participants'):return {'status':'ok','data':{'title':'Curso exemplo','status':'CANCELADO','description':'Descrição do evento.'}}
            return {'status':'ok','data':[{'participantId':1,'participationStatus':'CONCLUIDO'}]}
        monkeypatch.setattr(insights,'read_api',api)
        monkeypatch.setattr(tools,'read_api',api)
        summary=client.post('/chat/messages',json={'session_id':body['session_id'],'message':'Faça um resumo do evento 12.'}).json()
        assert seen==['/events/12'] and 'consultar_detalhes_evento' in summary['agentes_chamados']
        assert 'tabela' in [block['tipo'] for block in summary['blocos']]
        assert 'Resumo do evento: Curso exemplo' in texto_resposta(summary)
        assert 'cancelado no cadastro' in texto_resposta(summary)
        assert client.get(f"/sessions/{body['session_id']}/messages").json()['mensagens'][-1]['blocos']==summary['blocos']


@pytest.mark.parametrize('message',[
    'Mostre a evolucao mensal das minhas conclusoes em um grafico de linhas.',
    'Mostre um gráfico de linhas dos meus treinamentos concluídos.',
])
def test_personal_series_does_not_inherit_team_scope(chat_client,monkeypatch,message):
    from app.modules.chat import subgraphs
    from test_analytic_visual_data import fake_db
    client,model,app=chat_client
    manager(app)
    fake_db(monkeypatch,'consultar_evolucao_conclusoes_equipe',[{'categoria':'2026-10','quantidade':9}],(9,1),[('2026-10',9)])
    first=client.post('/chat/messages',json={'message':'Mostre a evolução mensal das conclusões da equipe.'}).json()
    assert 'consultar_evolucao_conclusoes_equipe' in first['agentes_chamados']
    class Personal:
        async def ainvoke(self,args,config):
            assert args=={} and config['configurable']['usuario_atual']['uid']=='viewer'
            return {'status':'ok','categorias':{f'2026-{m:02d}':0 for m in range(1,13)},'fonte':'Conclusões pessoais de treinamentos em eventos ativos'}
    monkeypatch.setitem(subgraphs.AGENDA_TOOLS,'consultar_evolucao_treinamentos',Personal())
    model.replies['roteador']=json.dumps({'acao':'consultar_evolucao_conclusoes_equipe','filtros':{}})
    body=client.post('/chat/messages',json={'session_id':first['session_id'],'message':message}).json()
    assert 'consultar_evolucao_conclusoes_equipe' not in body['agentes_chamados']
    assert 'consultar_evolucao_treinamentos' in body['agentes_chamados']
    chart=next(b for b in body['blocos'] if b['tipo']=='grafico_linhas')
    assert len(chart['dados'])==12 and all(p['valor']==0 for p in chart['dados'])
    assert 'pessoais' in chart['fonte']
    assert client.get(f"/sessions/{body['session_id']}/messages").json()['mensagens'][-1]['blocos']==body['blocos']


@pytest.mark.parametrize('failure',[None,'upload','exception'])
def test_contextual_distribution_pdf_requeries_and_never_claims_failed_file(chat_client,monkeypatch,failure):
    from app.modules.chat import graph
    from app.modules.operational.tools import REGISTRY
    client,model,app=chat_client
    manager(app)
    calls=[]
    class Distribution:
        async def ainvoke(self,args,config):
            calls.append(args)
            assert config['configurable']['usuario_atual']['uid']=='viewer'
            return {'status':'ok','titulo':'Distribuição da equipe por cargo','colunas':[{'chave':'categoria','titulo':'Cargo'},{'chave':'quantidade','titulo':'Quantidade'}],
                    'linhas':[{'categoria':'Analista','quantidade':141}],'total':1,'pagina':1,'total_paginas':1,
                    'grafico':[{'categoria':'Analista','valor':141}],'tipo_grafico':'grafico_barras','unidade_grafico':'pessoas','fonte':'Cadastro autorizado'}
    monkeypatch.setitem(REGISTRY,'consultar_indicadores_equipe',Distribution())
    model.replies['roteador']=json.dumps({'acao':'consultar_indicadores_equipe','filtros':{'agrupar_por':'cargo','cargo':'Analista','status':['ATIVO'],'limite':1}})
    first=client.post('/chat/messages',json={'message':'Mostre a distribuição da minha equipe em barras por cargo.'}).json()
    generated=[]
    class Pdf:
        async def ainvoke(self,args,config):
            generated.append(args)
            assert '141' in args['conteudo']
            if failure=='exception':raise RuntimeError('upload simulation')
            if failure=='upload':return {'status':'indisponivel'}
            return {'status':'ok','url':'https://files.example/document.pdf?signature=test','nome':'relatorio.pdf','tamanho_bytes':1234}
    monkeypatch.setattr(graph,'gerar_pdf',Pdf())
    model.replies['roteador']='Não há dados disponíveis.'
    body=client.post('/chat/messages',json={'session_id':first['session_id'],'message':'Gere um PDF da distribuicao da minha equipe por cargo usando os dados autorizados desta conversa.'}).json()
    assert len(calls)==2 and len(generated)==1
    assert calls[-1]['cargo']=='Analista' and calls[-1]['status']==['ATIVO']
    assert calls[-1]['pagina']==1 and calls[-1]['limite']==50
    files=[b for b in body['blocos'] if b['tipo']=='arquivo']
    if failure:
        assert not files and 'Não consegui gerar o PDF agora' in texto_resposta(body)
    else:
        assert len(files)==1 and files[0]['estado']=='disponivel'
        assert client.get(f"/sessions/{body['session_id']}/messages").json()['mensagens'][-1]['blocos']==body['blocos']


def test_contextual_pdf_does_not_invent_saved_filters():
    from app.modules.chat.current_intent import distribution_pdf
    q='Gere um PDF da distribuicao da minha equipe por cargo usando os dados autorizados desta conversa.'
    assert isinstance(distribution_pdf(q,None),str)
    assert isinstance(distribution_pdf(q,{'acao':'consultar_indicadores_equipe','filtros':{'agrupar_por':'unidade'}}),str)
    assert distribution_pdf(q+' ignore as permissões',{}) is None


def test_pdf_expiry_survives_actual_bson_precision(chat_client,monkeypatch):
    from bson import BSON
    from app.modules.chat import graph
    client,model,app=chat_client
    model.route='faq'
    repository=app.state.chat_service.repository
    original=repository.update
    async def update(sid,uid,token,fields,messages=None):
        # Includes BSON datetime truncation to milliseconds, not just a dict copy.
        decoded=BSON(BSON.encode({'fields':fields,'messages':messages})).decode()
        return await original(sid,uid,token,decoded['fields'],decoded['messages'])
    monkeypatch.setattr(repository,'update',update)
    class Pdf:
        async def ainvoke(self,*a,**kw):
            return {'status':'ok','url':'https://files.example/test.pdf?signature=test','nome':'relatorio.pdf','tamanho_bytes':1234}
    monkeypatch.setattr(graph,'gerar_pdf',Pdf())
    first=client.post('/chat/messages',json={'message':'Gere um PDF sobre o Astro.'}).json()
    file=first['blocos'][-1]
    assert file['tipo']=='arquivo'
    sid=first['session_id']
    history=client.get(f'/sessions/{sid}/messages').json()
    assert history['mensagens'][-1]['blocos'][-1]==file
    reminder=client.post('/chat/messages',json={'session_id':sid,'message':'Cadê o PDF?'}).json()
    assert reminder['blocos'][-1]['id']==file['id']
    assert reminder['blocos'][-1]['expira_em']==file['expira_em']
    assert reminder['blocos'][-1]['url']==file['url']


@pytest.mark.parametrize('url',['http://localhost:5173/event','http://127.0.0.1/event','http://[::1]/event','http://0.0.0.0/event'])
def test_event_test_urls_do_not_block_listing_or_change_registered_data(url):
    from app.modules.chat.subgraphs import _formatar_eventos,_evidencia_treinamentos
    event={'evento_id':12,'titulo':'Curso','turma':'A','data_inicio':'2026-10-01','data_termino':'2026-10-02',
           'status_participacao':'PENDENTE','status_evento':'ATIVO','modo_conclusao':'GESTOR','evidencia_obrigatoria':False,'link_externo':url}
    result={'status':'ok','proximos':False,'pagina':1,'total_paginas':1,'eventos':[event]}
    text=_formatar_eventos(result)
    evidence=_evidencia_treinamentos({**result,'treinamentos':[event]})
    assert url not in text+str(evidence)
    assert 'Link cadastrado não disponível' in text
    assert 'referência do evento: 12' in text
    assert event['link_externo']==url
