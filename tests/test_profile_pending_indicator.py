import json

import pytest
from pydantic import ValidationError

from test_chat import chat_client, texto_resposta
from app.api import auth
from app.core.security import CurrentUser
from app.modules.chat import subgraphs
from app.modules.chat.profile_pending import summarize_nr_pending
from app.modules.chat.schemas import PendenciasFicha, BlocoFichaUsuario


def situation(states):
    return {'status': 'ok', 'usuario': 'Pessoa Exemplo', 'cargo': 'Analista', 'unidade': 'Matriz',
            'quantidade': len(states), 'data_referencia': '2026-11-02',
            'nrs': [{'numero': i + 1, 'titulo': f'Norma {i + 1}', 'situacao': s} for i, s in enumerate(states)]}


@pytest.mark.parametrize('states,count', [([], 0), (['VIGENTE'], 0),
    (['PENDENTE', 'RENOVACAO_NECESSARIA', 'REALIZACAO_NECESSARIA', 'VIGENTE'], 3)])
def test_count_is_distinct_nrs_not_training_or_form_total(states, count):
    result = situation(states)
    assert summarize_nr_pending(result)['quantidade'] == count
    if states:
        result['nrs'] *= 2
        result['quantidade'] *= 2
        assert summarize_nr_pending(result)['quantidade'] == count


@pytest.mark.parametrize('result', [{'status': 'indisponivel'}, {'status': 'sem_dados'},
    {'status': 'ok'}, {'status': 'ok', 'nrs': [{'numero': 1, 'situacao': 'DESCONHECIDA'}]},
    {'status': 'ok', 'nrs': [], 'quantidade': 10},
    {'status': 'ok', 'nrs': [{'numero': 1, 'situacao': 'VIGENTE'}, {'numero': 1, 'situacao': 'PENDENTE'}]}])
def test_unknown_or_partial_count_is_not_zero(result):
    summary = summarize_nr_pending(result)
    assert summary['status'] == 'indisponivel' and summary['quantidade'] is None


@pytest.mark.parametrize('value', [{'status': 'ok'}, {'status': 'ok', 'quantidade': -1},
    {'status': 'ok', 'quantidade': True}, {'status': 'indisponivel', 'quantidade': 0}])
def test_indicator_schema_rejects_invalid_or_false_zero(value):
    with pytest.raises(ValidationError):
        PendenciasFicha.model_validate(value)


@pytest.mark.parametrize('role', ['COLABORADOR', 'GESTOR', 'GESTOR_WORKSPACE', 'ADMIN'])
@pytest.mark.parametrize('mode', ['ok', 'zero', 'unavailable', 'exception', 'timeout', 'judge'])
def test_own_card_uses_only_current_identity_and_preserves_history(chat_client, monkeypatch, role, mode):
    client, model, app = chat_client
    app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid='user-a', role=role)
    calls = []

    class Own:
        async def ainvoke(self, args, config):
            return {'status': 'ok', 'dados': {'nome': 'Pessoa Exemplo', 'email': 'pessoa@example.com', 'tipo': role, 'cargo': 'Analista'}}

    class Pending:
        async def ainvoke(self, args, config):
            calls.append((args, config))
            if mode == 'exception':
                raise RuntimeError('private backend failure')
            if mode == 'timeout':
                raise TimeoutError('private timeout')
            return {'status': 'indisponivel'} if mode == 'unavailable' else situation(['VIGENTE'] if mode == 'zero' else ['PENDENTE', 'VIGENTE'])

    monkeypatch.setitem(subgraphs.RH_TOOLS, 'buscar_meus_dados', Own())
    monkeypatch.setitem(subgraphs.SST_TOOLS, 'consultar_situacao_nrs', Pending())
    if mode == 'judge':
        model.replies['juiz'] = json.dumps({'status': 'revisar', 'motivo': 'Teste', 'problemas': ['Não validado']})
    response = client.post('/chat/messages', json={'message': 'quero minha ficha'})
    assert response.status_code == 200
    body = response.json()
    assert len(calls) == (0 if role == 'ADMIN' else 1)
    if calls:
        assert calls[0][0] == {} and calls[0][1]['configurable']['usuario_atual']['uid'] == 'user-a'
    cards = [b for b in body['blocos'] if b['tipo'] == 'ficha_usuario']
    if mode == 'judge':
        assert not cards
        assert 'Pendências de NRs:' not in texto_resposta(body)
        return
    card = cards[0]
    pending = card['pendencias']
    if role == 'ADMIN':
        assert pending['status'] == 'nao_aplicavel' and pending['quantidade'] is None
        assert 'não se aplica' in body['blocos'][-1]['conteudo']
    elif mode in {'unavailable', 'exception', 'timeout'}:
        assert pending['status'] == 'indisponivel' and pending['quantidade'] is None
        assert 'Não foi possível confirmar' in body['blocos'][-1]['conteudo']
        assert 'Pendências de NRs: 0' not in texto_resposta(body)
    else:
        expected = 0 if mode == 'zero' else 1
        assert pending['quantidade'] == expected and pending['data_referencia'] == '2026-11-02'
        assert f'Pendências de NRs: {expected}.' in body['blocos'][-1]['conteudo']
    assert [b['tipo'] for b in body['blocos']] == ['texto', 'ficha_usuario', 'texto']
    from app.modules.chat.profile_pending import pending_caption
    assert body['blocos'][-1]['conteudo'] == pending_caption(pending)
    judge = next(messages for agent, messages, _ in model.calls if agent == 'juiz')
    assert pending_caption(pending) in judge[-1].content
    assert card['conformidade'] is None and card['situacao'] is None
    assert card['origem'] == 'usuario_autenticado' and card['foto']['url'] is None
    assert 'private backend failure' not in response.text
    assert client.get(f"/sessions/{body['session_id']}/messages").json()['mensagens'][-1]['blocos'] == body['blocos']
    assert len(calls) == (0 if role == 'ADMIN' else 1)  # History doesn't recount.
    app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid='user-b', role=role)
    assert client.get(f"/sessions/{body['session_id']}/messages").status_code == 404


def test_specific_field_does_not_query_pending_and_legacy_card_remains_valid(chat_client, monkeypatch):
    client, model, _ = chat_client
    model.replies['rh'] = json.dumps({'acao': 'buscar_meus_dados', 'filtros': None, 'resposta': None})
    class Own:
        async def ainvoke(self, *args, **kwargs):
            return {'status': 'ok', 'dados': {'nome': 'Pessoa Exemplo', 'cargo': 'Analista'}}
    class Forbidden:
        async def ainvoke(self, *args, **kwargs):
            pytest.fail('Specific field must not query pending')
    monkeypatch.setitem(subgraphs.RH_TOOLS, 'buscar_meus_dados', Own())
    monkeypatch.setitem(subgraphs.SST_TOOLS, 'consultar_situacao_nrs', Forbidden())
    body = client.post('/chat/messages', json={'message': 'Qual é meu cargo?'}).json()
    assert 'Analista' in texto_resposta(body) and all(b['tipo'] == 'texto' for b in body['blocos'])
    assert BlocoFichaUsuario.model_validate({'foto': {'tipo': 'imagem', 'url': None, 'descricao': 'Foto'}}).pendencias is None


def test_third_party_card_reuses_authorized_query_without_own_pending_call(chat_client, monkeypatch):
    client, _, app = chat_client
    app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid='manager-a', role='GESTOR')
    calls = []
    class Third:
        async def ainvoke(self, args, config):
            calls.append((args, config))
            return {**situation(['PENDENTE', 'RENOVACAO_NECESSARIA']), 'consulta_terceiro': True}
    monkeypatch.setitem(subgraphs.SST_TOOLS, 'consultar_conformidade_usuario', Third())
    body = client.post('/chat/messages', json={'message': 'Mostre a ficha de Pessoa Exemplo'}).json()
    assert len(calls) == 1 and calls[0][1]['configurable']['usuario_atual']['uid'] == 'manager-a'
    assert next(b for b in body['blocos'] if b['tipo'] == 'ficha_usuario')['pendencias']['quantidade'] == 2
    assert texto_resposta(body).count('Pendências de NRs: 2.') == 1
