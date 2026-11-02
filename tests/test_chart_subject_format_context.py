import json
import asyncio

import pytest

from test_chat import chat_client, texto_resposta
from app.api import auth
from app.core.security import CurrentUser
from app.modules.chat.chart_menu import chart_style_request, make_chart_menu, select_chart
from app.modules.operational.tools import REGISTRY, OperationalDecision
from app.modules.operational import insights


@pytest.mark.parametrize('question', ['quero um de rosco', 'quero um de rosca', 'mostre um grafico de donut'])
@pytest.mark.parametrize('approve', [True, False])
def test_format_then_subject_executes_coverage_without_repeated_clarification(chat_client, monkeypatch, question, approve):
    client, model, app = chat_client
    app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid='user-a', role='GESTOR')
    calls = []

    class Tool:
        async def ainvoke(self, args, config):
            calls.append((args, config))
            return {'status': 'ok', 'titulo': 'Pares pessoa/NR sem encaminhamento por NR',
                    'colunas': [{'chave': 'categoria', 'titulo': 'NR'}, {'chave': 'quantidade', 'titulo': 'Pares pessoa/NR'}],
                    'linhas': [{'categoria': 'NR-6', 'quantidade': 3}, {'categoria': 'NR-12', 'quantidade': 2}],
                    'total': 2, 'pagina': 1, 'total_paginas': 1, 'fonte': 'Consulta sintética autorizada',
                    'tipo_grafico': args['tipo_grafico'], 'unidade_grafico': 'pendências',
                    'grafico': [{'categoria': 'NR-6', 'valor': 3}, {'categoria': 'NR-12', 'valor': 2}]}

    monkeypatch.setitem(REGISTRY, 'consultar_indicadores_cobertura', Tool())
    first = client.post('/chat/messages', json={'message': 'que tipo de graficos?'}).json()
    second = client.post('/chat/messages', json={'message': question, 'session_id': first['session_id']}).json()
    assert 'rosca' in texto_resposta(second) and not calls
    assert app.state.chat_service.repository.docs[first['session_id']]['menu_graficos']['tipo_grafico'] == 'grafico_rosca'
    if not approve:
        model.replies['juiz'] = json.dumps({'status': 'rejeitado', 'motivo': 'Teste', 'problemas': ['Não validado.']})
    body = client.post('/chat/messages', json={'message': 'cobertura', 'session_id': first['session_id']}).json()
    assert len(calls) == 1 and calls[0][0]['tipo_grafico'] == 'grafico_rosca'
    assert calls[0][1]['configurable']['usuario_atual']['uid'] == 'user-a'
    assert not any(c[0] in {'roteador', 'agenda'} for c in model.calls)
    assert any(b['tipo'] == 'grafico_rosca' for b in body['blocos']) == approve
    if approve:
        chart = next(b for b in body['blocos'] if b['tipo'] == 'grafico_rosca')
        assert [p['valor'] for p in chart['dados']] == [3, 2]
        history = client.get(f"/sessions/{body['session_id']}/messages").json()
        assert history['mensagens'][-1]['blocos'] == body['blocos']


def test_format_menu_does_not_offer_month_series_as_rosca_or_leak_team_choices():
    _, menu = make_chart_menu('COLABORADOR', 'grafico_rosca')
    assert menu['opcoes'] == ['treinamentos_pessoais']
    assert select_chart('1', menu, 'COLABORADOR') == 'grafico_rosca'
    manager_menu = make_chart_menu('GESTOR', 'grafico_rosca')[1]
    assert 'conclusoes_pessoais' not in manager_menu['opcoes']
    assert isinstance(select_chart('cobertura', manager_menu, 'COLABORADOR'), str)
    assert chart_style_request('quero um de rosco de outra empresa') is None
    assert chart_style_request('não quero um de rosco') is None
    assert isinstance(select_chart('cobertura', manager_menu, 'GESTOR'), OperationalDecision)


def test_real_coverage_tool_accepts_rosca_without_changing_scope_or_query(monkeypatch):
    calls = []

    def read(action, args, user):
        calls.append((action, args, user))
        return {'status': 'sem_dados'}

    monkeypatch.setattr(insights, 'sql_read', read)
    config = {'configurable': {'usuario_atual': {'uid': 'user-a', 'role': 'GESTOR'}}}
    result = asyncio.run(REGISTRY['consultar_indicadores_cobertura'].ainvoke({'tipo_grafico': 'grafico_rosca'}, config=config))
    assert result['status'] == 'sem_dados' and len(calls) == 1
    assert calls[0][0] == 'consultar_indicadores_cobertura'
    assert calls[0][1].tipo_grafico == 'grafico_rosca' and calls[0][2].uid == 'user-a'
    config['configurable']['usuario_atual']['role'] = 'COLABORADOR'
    denied = asyncio.run(REGISTRY['consultar_indicadores_cobertura'].ainvoke({'tipo_grafico': 'grafico_rosca'}, config=config))
    assert denied['status'] == 'nao_autorizado' and len(calls) == 1
