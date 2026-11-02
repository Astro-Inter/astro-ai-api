import asyncio
import json
from uuid import uuid4

import pytest

from test_chat import chat_client, texto_resposta
from app.api import auth
from app.core.security import CurrentUser
from app.modules.chat.chart_menu import legacy_expiry_chart
from app.modules.chat.current_intent import change_chart_type
from app.modules.operational.tools import REGISTRY


PERIOD = 'Para qual período você quer consultar os vencimentos da equipe? Por exemplo, próximos 30 dias.'
OFFER = ('Posso consultar um gráfico de rosca com dados autorizados da sua equipe, se a consulta permitir. '
         'O que você quer visualizar: pendências de NR, vencimentos ou outro indicador? '
         'Qual informação você quer ver no gráfico de rosca?')
PROMISE = 'Vou consultar os vencimentos atuais da sua equipe para o gráfico de rosca.'


def stub_expiry(monkeypatch, *, unavailable=False):
    calls = []

    class Tool:
        async def ainvoke(self, args, config):
            calls.append((args, config))
            if unavailable:
                return {'status': 'indisponivel', 'mensagem': 'Consulta temporariamente indisponível.'}
            return {'status': 'ok', 'titulo': 'Vencimentos por faixa de prazo', 'fonte': 'Cadastro autorizado',
                    'colunas': [{'chave': 'categoria', 'titulo': 'Prazo'}, {'chave': 'quantidade', 'titulo': 'Registros'}],
                    'linhas': [{'categoria': 'Vencidas', 'quantidade': 2}, {'categoria': 'Sem validade', 'quantidade': 1}],
                    'total': 2, 'pagina': 1, 'total_paginas': 1, 'tipo_grafico': args['tipo_grafico'],
                    'unidade_grafico': 'registros', 'grafico': [{'categoria': 'Vencidas', 'valor': 2}, {'categoria': 'Sem validade', 'valor': 1}]}

    monkeypatch.setitem(REGISTRY, 'consultar_grafico_vencimentos', Tool())
    return calls


@pytest.mark.parametrize('phrase', ['queri de rosca', 'queria de rosca', 'quero um de rosca'])
@pytest.mark.parametrize('role', ['GESTOR', 'GESTOR_WORKSPACE'])
def test_shape_then_expiry_queries_immediately_without_asking_for_period(chat_client, monkeypatch, phrase, role):
    client, model, app = chat_client
    app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid='user-a', role=role)
    calls = stub_expiry(monkeypatch)
    first = client.post('/chat/messages', json={'message': phrase}).json()
    assert not calls and 'rosca' in texto_resposta(first)
    body = client.post('/chat/messages', json={'message': 'vencimentos', 'session_id': first['session_id']}).json()
    assert len(calls) == 1 and calls[0][0]['tipo_grafico'] == 'grafico_rosca'
    assert calls[0][1]['configurable']['usuario_atual'] == {'uid': 'user-a', 'role': role}
    chart = next(b for b in body['blocos'] if b['tipo'] == 'grafico_rosca')
    assert [p['valor'] for p in chart['dados']] == [2, 1]
    assert 'qual período' not in texto_resposta(body) and 'Vou consultar' not in texto_resposta(body)
    assert not any(c[0] == 'roteador' for c in model.calls)
    stored = app.state.chat_service.repository.docs[first['session_id']]
    assert stored['consulta_prazo'] is False and stored['menu_graficos'] is None
    assert client.get(f"/sessions/{first['session_id']}/messages").json()['mensagens'][-1]['blocos'] == body['blocos']


@pytest.mark.parametrize('stage', ['offer', 'period', 'promise'])
@pytest.mark.parametrize('barrier', [None, 'judge', 'input', 'unavailable'])
def test_approved_legacy_clarification_can_finish_same_session(chat_client, monkeypatch, stage, barrier):
    client, model, app = chat_client
    app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid='user-a', role='GESTOR')
    calls = stub_expiry(monkeypatch, unavailable=barrier == 'unavailable')
    sid = str(uuid4())
    repo = app.state.chat_service.repository
    asyncio.run(repo.ensure(sid, 'user-a'))
    history = [{'role': 'human', 'content': 'queri de rosca'}, {'role': 'assistant', 'content': OFFER}]
    message = 'vencimentos'
    if stage in {'period', 'promise'}:
        history += [{'role': 'human', 'content': 'vencimentos'}, {'role': 'assistant', 'content': PERIOD}]
        message = 'atualmente'
    if stage == 'promise':
        history += [{'role': 'human', 'content': 'atualmente'}, {'role': 'assistant', 'content': PROMISE}]
        message = 'ok'
    repo.docs[sid].update(mensagens=history, consulta_prazo=stage == 'period')
    if barrier == 'judge':
        model.replies['juiz'] = json.dumps({'status': 'revisar', 'motivo': 'Teste', 'problemas': ['Não validado']})
    elif barrier == 'input':
        model.replies['guardrail_entrada'] = json.dumps({'decisao': 'bloquear', 'motivo': 'Teste', 'mensagem': 'Não permitido.'})
    response = client.post('/chat/messages', json={'message': message, 'session_id': sid})
    assert response.status_code == 200
    body = response.json()
    assert len(calls) == (0 if barrier == 'input' else 1)
    if calls:
        assert calls[0][0]['tipo_grafico'] == 'grafico_rosca'
    assert any(b['tipo'] == 'grafico_rosca' for b in body['blocos']) == (barrier is None)
    if barrier is None:
        assert client.get(f'/sessions/{sid}/messages').json()['mensagens'][-1]['blocos'] == body['blocos']
    if barrier in {'judge', 'input'}:
        assert repo.docs[sid]['mensagens'] == history
    assert not any(c[0] in {'roteador', 'sst'} for c in model.calls)


def test_legacy_recovery_requires_current_authorized_context():
    history = [{'role': 'assistant', 'content': PROMISE}]
    assert legacy_expiry_chart('ok', history, 'COLABORADOR') is None
    assert legacy_expiry_chart('ok', [], 'GESTOR') is None
    assert legacy_expiry_chart('ok de outra empresa', history, 'GESTOR') is None
    assert legacy_expiry_chart('atualmente', [{'role': 'assistant', 'content': PERIOD}], 'GESTOR', False) is None
    assert legacy_expiry_chart('ok', history + [{'role': 'assistant', 'content': 'Outro assunto.'}], 'GESTOR') is None
    previous = {'acao': 'consultar_grafico_vencimentos', 'filtros': {'unidade': 'Unidade A', 'cargo': 'Técnico'}}
    changed = change_chart_type('queri de rosca', previous, 'consulta_operacional')
    assert changed.filtros.tipo_grafico == 'grafico_rosca'
    assert changed.filtros.unidade == 'Unidade A' and changed.filtros.cargo == 'Técnico'
