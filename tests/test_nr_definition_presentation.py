import copy
import json
from unittest.mock import AsyncMock

import pytest

from test_chat import chat_client, texto_resposta
from app.modules.chat import subgraphs
from test_nr_detail_evidence import detail


def revoked():
    result = detail()
    result['nrs'][0].update(numero=2, nome='Inspeção Prévia', situacao='REVOGADA', revogada=True,
                            descricao='Contexto interno antigo.', usabilidade=['Campo cadastral'], tempo_reciclagem_meses=24)
    return result


@pytest.mark.parametrize('question', ['O que é a NR 2?', 'Sobre o que é a NR-2?', 'É sobre o que a NR2?', 'Explique a NR 2'])
@pytest.mark.parametrize('approve', [True, False])
def test_nr_definition_is_short_revoked_and_still_reviewed(chat_client, monkeypatch, question, approve):
    client, model, app = chat_client
    result = revoked()
    original = copy.deepcopy(result)
    tool = AsyncMock()
    tool.ainvoke.return_value = result
    monkeypatch.setitem(subgraphs.SST_TOOLS, 'consultar_nrs', tool)
    model.route = 'sst'
    if not approve:
        model.replies['juiz'] = json.dumps({'status': 'revisar', 'motivo': 'Preferência de concisão.', 'problemas': ['Teste de revisão.']})
    body = client.post('/chat/messages', json={'message': question}).json()
    assert tool.ainvoke.call_args.args[0]['numeros'] == [2]
    judge = next(c for c in model.calls if c[0] == 'juiz')
    payload = json.loads(judge[1][-1].content.split('\n', 1)[1])
    candidate = payload['resposta_candidata'] if 'resposta_candidata' in payload else payload['candidato']
    assert 'revogada' in candidate and 'Inspeção Prévia' in candidate
    assert 'Reciclagem' not in candidate and 'Usabilidade' not in candidate
    assert 'Contexto interno antigo' not in candidate and len(candidate) < 600
    assert payload['resultado']['evidencia_tool']['resultado']['nrs'][0]['situacao'] == 'REVOGADA'
    text = texto_resposta(body)
    if approve:
        assert 'NR-2' in text and 'revogada' in text
        history = client.get(f"/sessions/{body['session_id']}/messages").json()
        assert history['mensagens'][-1]['blocos'] == body['blocos']
    else:
        assert text.startswith('Consultei as informações da norma')
        assert 'componente' not in text and 'Preferência de concisão' not in text
        assert not app.state.chat_service.repository.docs[body['session_id']]['mensagens']
    assert result == original


def test_detailed_request_keeps_its_contract_and_empty_results_are_not_invented():
    assert not subgraphs._pedido_definicao_nr('Quero detalhes da NR2')
    assert not subgraphs._pedido_definicao_nr('O que é a NR 2 e como se aplica ao meu cargo?')
    assert 'Reciclagem' in subgraphs._formatar_nrs(revoked())
    assert subgraphs._formatar_nrs({'status': 'sem_dados'}, simples=True) == 'Não encontrei NRs com os filtros informados.'
