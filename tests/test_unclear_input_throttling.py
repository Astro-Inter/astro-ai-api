import json

import pytest

from test_chat import chat_client, texto_resposta
from app.modules.chat.errors import ChatError


@pytest.mark.parametrize('stage', ['guardrail_entrada', 'roteador'])
def test_unclear_input_rate_limit_has_safe_explanation_and_can_be_retried(chat_client, stage):
    client, model, app = chat_client
    first = client.post('/chat/messages', json={'message': 'Quem é você?'}).json()
    sid = first['session_id']
    before = len(app.state.chat_service.repository.docs[sid]['mensagens'])
    original = model.complete

    async def throttled(agent, *args, **kwargs):
        if agent == stage:
            raise ChatError(503, 'Untrusted provider payload: private-test', reason='rate_limited',
                            retry_after=23, stage=stage, provider_status_code=429)
        return await original(agent, *args, **kwargs)

    model.complete = throttled
    question = 'Hshshehehe bdbdne'
    response = client.post('/chat/messages', json={'message': question, 'session_id': sid})
    assert response.status_code == 503 and response.headers['retry-after'] == '23'
    assert response.headers['x-request-id']
    assert 'limite temporário de solicitações' in response.json()['detail']
    assert 'private-test' not in response.text
    assert 'blocos' not in response.json()
    assert len(app.state.chat_service.repository.docs[sid]['mensagens']) == before
    model.complete = original
    model.replies['guardrail_entrada'] = json.dumps({'decisao': 'esclarecer', 'motivo': 'contexto_insuficiente',
                                                     'mensagem': 'Não entendi sua mensagem. Pode reformular sua pergunta?'})
    retry = client.post('/chat/messages', json={'message': question, 'session_id': sid})
    assert retry.status_code == 200
    assert texto_resposta(retry.json()) == 'Não entendi sua mensagem. Pode reformular sua pergunta?'


def test_meaningless_input_can_request_clarification_without_domain_or_tools(chat_client):
    client, model, _ = chat_client
    model.replies['guardrail_entrada'] = json.dumps({'decisao': 'esclarecer', 'motivo': 'contexto_insuficiente',
                                                   'mensagem': 'Não entendi sua mensagem. Pode explicar o que precisa?'})
    response = client.post('/chat/messages', json={'message': 'Hshshehehe bdbdne'})
    assert response.status_code == 200
    assert 'Não entendi sua mensagem' in texto_resposta(response.json())
    assert [call[0] for call in model.calls] == ['guardrail_entrada']
