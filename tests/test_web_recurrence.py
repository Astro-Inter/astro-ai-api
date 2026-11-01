import json
from pathlib import Path

import pytest

from test_chat import chat_client, no_remote_tracing
from app.modules.chat.errors import ChatError
from app.scripts.plan_pdf_cors import plan_policy, download_rule, RULE_ID


def test_cors_plan_preserves_unrelated_rules_and_never_applies():
    original={'CORSRules':[{'ID':'other-service','AllowedOrigins':['https://other.example'],'AllowedMethods':['PUT']}]}
    saved=json.loads(json.dumps(original))
    plan=plan_policy(original,['https://astro-inter.github.io'])
    assert not plan['applied'] and original==saved
    assert plan['proposed']['CORSRules'][0]==original['CORSRules'][0]
    rule=plan['proposed']['CORSRules'][1]
    assert rule['AllowedMethods']==['GET'] and rule['AllowedHeaders']==['Range']
    assert rule['AllowedOrigins']==['https://astro-inter.github.io']


@pytest.mark.parametrize('origin',['*','null','https://*.example','https://example/path','https://user:password@example','http://example','https://example/?secret=value'])
def test_cors_candidate_rejects_unsafe_origins(origin):
    with pytest.raises(ValueError):download_rule([origin])


def test_cors_candidate_matches_versioned_rule_and_updates_only_its_own_id():
    expected=json.loads(Path('infra/r2/pdf-download-cors.s3.json').read_text())
    assert expected['CORSRules']==[download_rule(['https://astro-inter.github.io'])]
    current={'CORSRules':[{'ID':RULE_ID,'AllowedOrigins':['https://old.example'],'AllowedMethods':['GET']}]}
    assert plan_policy(current,['https://astro-inter.github.io'])['proposed']==expected
    wildcard={'CORSRules':[{'AllowedOrigins':['*'],'AllowedMethods':['GET']}]}
    assert plan_policy(wildcard,['https://astro-inter.github.io'])['warnings']


def test_provider_503_has_safe_correlated_stage_and_preserves_retry_after(chat_client,monkeypatch,caplog):
    client,model,app=chat_client
    original=model.complete
    async def unavailable(agent,*args,**kwargs):
        if agent=='roteador':
            raise ChatError(503,'Serviço temporariamente indisponível.',reason='rate_limited',stage='roteador',retry_after=17)
        return await original(agent,*args,**kwargs)
    monkeypatch.setattr(model,'complete',unavailable)
    response=client.post('/chat/messages',json={'message':'Preciso de orientação.'})
    assert response.status_code==503 and response.headers['retry-after']=='17'
    record=next(r for r in caplog.records if r.getMessage()=='chat_request_rejected')
    assert record.error_stage=='roteador' and record.error_reason=='rate_limited'
    assert record.request_id==response.headers['x-request-id']


def test_error_diagnostics_never_log_untrusted_reason_or_stage(chat_client,monkeypatch,caplog):
    client,model,app=chat_client
    async def unavailable(*args,**kwargs):
        raise ChatError(503,'Falha.',reason='Bearer private-test',stage='https://files.example?signature=private-test')
    monkeypatch.setattr(model,'complete',unavailable)
    response=client.post('/chat/messages',json={'message':'Preciso de orientação.'})
    assert response.status_code==503
    record=next(r for r in caplog.records if r.getMessage()=='chat_request_rejected')
    assert record.error_stage=='chat' and record.error_reason=='unspecified'
    assert 'private-test' not in caplog.text


def test_complete_distribution_summary_distinguishes_people_from_categories(chat_client,monkeypatch):
    from app.api import auth
    from app.core.security import CurrentUser
    from app.modules.operational.tools import REGISTRY
    client,model,app=chat_client
    app.dependency_overrides[auth.get_current_user]=lambda:CurrentUser(uid='viewer',role='GESTOR_WORKSPACE')
    class Distribution:
        async def ainvoke(self,args,config):
            return {'status':'ok','titulo':'Distribuição por cargo','colunas':[{'chave':'categoria','titulo':'Cargo'},{'chave':'quantidade','titulo':'Pessoas'}],
                    'linhas':[{'categoria':'Analista','quantidade':100},{'categoria':'Operador','quantidade':41}],
                    'total':2,'pagina':1,'total_paginas':1,'completo':True,
                    'grafico':[{'categoria':'Analista','valor':100},{'categoria':'Operador','valor':41}],
                    'tipo_grafico':'grafico_barras','unidade_grafico':'pessoas','fonte':'Cadastro autorizado'}
    monkeypatch.setitem(REGISTRY,'consultar_indicadores_equipe',Distribution())
    model.replies['roteador']=json.dumps({'acao':'consultar_indicadores_equipe','filtros':{'agrupar_por':'cargo'}})
    body=client.post('/chat/messages',json={'message':'Mostre distribuição da equipe em barras por cargo.'}).json()
    review=next(call for call in model.calls if call[0]=='juiz')
    candidate=json.loads(review[1][-1].content.split('\n',1)[1])['resposta_candidata']
    assert '141 pessoas em 2 categorias' in candidate
    assert 'independentemente das linhas' in candidate
    assert 'grafico_barras' in [b['tipo'] for b in body['blocos']]
    assert client.get(f"/sessions/{body['session_id']}/messages").json()['mensagens'][-1]['blocos']==body['blocos']


def test_negative_review_is_correlated_without_logging_its_sensitive_text(chat_client,monkeypatch,caplog):
    from app.api import auth
    from app.core.security import CurrentUser
    from app.modules.operational.tools import REGISTRY
    client,model,app=chat_client
    app.dependency_overrides[auth.get_current_user]=lambda:CurrentUser(uid='viewer',role='GESTOR_WORKSPACE')
    class Tool:
        async def ainvoke(self,*a,**kw):return {'status':'sem_dados','mensagem':'Sem dados.'}
    monkeypatch.setitem(REGISTRY,'consultar_indicadores_equipe',Tool())
    model.replies['roteador']=json.dumps({'acao':'consultar_indicadores_equipe','filtros':{}})
    model.replies['juiz']=json.dumps({'status':'revisar','motivo':'sensitive-review-marker','problemas':['sensitive-review-marker']})
    response=client.post('/chat/messages',json={'message':'Mostre distribuição da equipe por cargo.'})
    assert response.status_code==200 and all(b['tipo']=='texto' for b in response.json()['blocos'])
    record=next(r for r in caplog.records if r.getMessage()=='chat_review_not_approved')
    assert record.review_status=='revisar' and record.issue_count==1
    assert record.tool=='consultar_indicadores_equipe' and record.request_id==response.headers['x-request-id']
    assert 'sensitive-review-marker' not in caplog.text
