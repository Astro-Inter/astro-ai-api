"""Política de listagem completa, com contrato v2 compatível até 150 linhas."""
import json
from contextlib import contextmanager

import pytest
from test_chat import chat_client
from app.api import auth
from app.core.security import CurrentUser
from app.modules.rh import tools


@pytest.mark.parametrize('requested', [1, 20, 100, 500])
@pytest.mark.parametrize('available', [141, 150, 151, 250])
def test_complete_authorized_listing_post_and_history(chat_client, monkeypatch, requested, available):
    client, model, app = chat_client
    app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid='viewer', role='GESTOR_WORKSPACE')
    rows = [(f'Pessoa {i:04}', f'p{i}@example.com', 'COLABORADOR', 'Cargo', 'Unidade', 'PRESENCIAL', 'ATIVO') for i in range(available)]
    calls = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query, params):
            self.query, self.params = query, params
            calls.append((query, params))
        def fetchone(self): return (available,)
        def fetchall(self):
            size, offset = self.params[-2:]
            assert 1 <= size <= 50
            assert 'ORDER BY usuario.nome, usuario.email, usuario.id_usuario' in self.query
            assert self.params[0] == 'viewer'
            return rows[offset:offset + size]
    class Connection:
        def cursor(self): return Cursor()
    @contextmanager
    def connect(): yield Connection()
    monkeypatch.setattr(tools.app_config, 'DATABASE_URL', 'postgresql://fake')
    monkeypatch.setattr(tools, 'get_conn', connect)
    response = client.post('/chat/messages', json={'message': f'Liste {requested} usuários da minha equipe em uma tabela.'})
    assert response.status_code == 200
    body = response.json()
    table = next(b for b in body['blocos'] if b['tipo'] == 'tabela')
    expected = min(available, 150)
    assert len(table['linhas']) == table['retornada'] == expected
    assert table['total'] == table['solicitada'] == available
    assert table['limitado'] == table['tem_mais'] == (available > 150)
    assert table['motivo_limite'] == ('maximo_resultados' if available > 150 else None)
    assert table['versao'] == 2 and table['paginacao'] == 'local'
    assert len({row['email'] for row in table['linhas']}) == expected
    text = ' '.join(b['conteudo'] for b in body['blocos'] if b['tipo'] == 'texto')
    assert 'quantidade solicitada' not in text.lower()
    assert ('parcial' in text) == (available > 150)
    assert len([q for q, p in calls if 'OFFSET' in q]) == (expected + 49) // 50
    history = client.get(f"/sessions/{body['session_id']}/messages").json()
    assert history['mensagens'][-1]['blocos'] == body['blocos']
    judge = next(messages for agent, messages, _ in model.calls if agent == 'juiz')
    evidence = json.loads(judge[-1].content.split('\n', 1)[1])
    assert evidence['resultado']['evidencia_tool']['resultado']['politica_quantidade'] == 'conjunto_autorizado'


def test_remainder_requeries_and_describes_replacement(chat_client, monkeypatch):
    from app.modules.chat import subgraphs
    client, model, app = chat_client
    app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid='viewer', role='GESTOR_WORKSPACE')
    calls = []
    class Tool:
        async def ainvoke(self, args, config):
            calls.append(config['configurable']['usuario_atual']['uid'])
            return {'status':'ok','consulta':'listar','total':1,'pagina':1,'total_paginas':1,
                    'solicitada':1,'retornada':1,'limitado':False,'tem_mais':False,'motivo_limite':None,
                    'politica_quantidade':'conjunto_autorizado','quantidade_mencionada':100,
                    'usuarios':[{'nome':'Pessoa','email':'p@example.com','tipo':'COLABORADOR','cargo':'Cargo',
                                 'unidade':'Unidade','modalidade':'PRESENCIAL','status':'ATIVO'}]}
    monkeypatch.setitem(subgraphs.RH_TOOLS, 'buscar_outros_usuarios', Tool())
    model.replies['rh'] = json.dumps({'acao':'buscar_outros_usuarios','filtros':{},'resposta':None})
    first = client.post('/chat/messages', json={'message':'Liste 100 usuários da minha equipe em uma tabela.'}).json()
    body = client.post('/chat/messages', json={'message':'Mostre o resto que falta','session_id':first['session_id']}).json()
    assert calls == ['viewer', 'viewer']
    assert 'esta tabela substitui a anterior' in str(body['blocos'])
    assert 'não é um lote adicional' in str(body['blocos'])


def test_remainder_preserves_saved_filters_without_model_reinterpretation():
    from app.modules.chat.graph import _continuacao_rh
    saved = tools.BuscarOutrosUsuariosArgs(status=['ATIVO'], cargo='Soldador', tipos=['COLABORADOR']).model_dump()
    state = {'mensagem':'Mostre o resto que falta','contexto':{'ultima_rota':'rh'},'consulta_rh':saved}
    assert _continuacao_rh(state).model_dump() == saved
    assert _continuacao_rh({**state,'contexto':{'ultima_rota':'agenda'}}) is None
    assert _continuacao_rh({**state,'mensagem':'Próxima página'}) is None
    assert _continuacao_rh({**state,'consulta_rh':{**saved,'pagina':2}}) is None
