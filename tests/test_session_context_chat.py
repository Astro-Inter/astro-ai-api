"""Session regressions using synthetic Mongo records and simulated tools/LLMs."""
import asyncio
import json
from uuid import uuid4

import pytest

from app.api import auth
from app.core.security import CurrentUser
from app.modules.chat.service import ChatService
from app.modules.chat.schemas import BlocoTabela
from app.modules.chat import graph, subgraphs
from app.modules.operational.tools import REGISTRY
from memory_fakes import FakeFaqVectors, FakeVectors
from test_chat import FakeModel, chat_client, no_remote_tracing


def prepare(repository, messages, **metadata):
    sid = str(uuid4())
    asyncio.run(repository.ensure(sid, "user-a"))
    repository.docs[sid].update(mensagens=messages, **metadata)
    return sid


class OwnProfile:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, args, config):
        self.calls.append(config["configurable"]["usuario_atual"]["uid"])
        return {"status": "ok", "dados": {"nome": "Pessoa Exemplo", "email": "pessoa@example.com",
                "cargo": "Engenheira", "tipo": "GESTOR_WORKSPACE"}, "foto_url": None}


class Indicators:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, args, config):
        self.calls.append((args, config["configurable"]))
        return {"status": "ok", "titulo": "Distribuição autorizada",
                "colunas": [{"chave": "categoria", "titulo": "Cargo"}, {"chave": "quantidade", "titulo": "Quantidade"}],
                "linhas": [{"categoria": "Analista", "quantidade": 141}], "total": 1, "pagina": 1, "total_paginas": 1,
                "tipo_grafico": "grafico_barras", "grafico": [{"categoria": "Analista", "valor": 141}],
                "unidade_grafico": "pessoas", "fonte": "Cadastro autorizado"}


@pytest.mark.parametrize("legacy", [False, True])
def test_list_profile_then_chart_requeries_all_authorized_rows(chat_client, monkeypatch, legacy):
    client, model, app = chat_client
    repo = app.state.chat_service.repository
    app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid="user-a", role="GESTOR_WORKSPACE")
    table = BlocoTabela(versao=2, titulo="Equipe", colunas=[{"chave": "nome", "titulo": "Nome"}, {"chave": "cargo", "titulo": "Cargo"}],
        linhas=[{"nome": f"Pessoa {i:03}", "cargo": "Analista"} for i in range(100)],
        pagina=1, total_paginas=1, total=141, solicitada=100, retornada=100, limitado=False, tem_mais=True, paginacao="local")
    filters = {"limite": 100} if legacy else {"limite": 100, "cargo": "Analista", "tipos": ["COLABORADOR"]}
    sid = prepare(repo, [{"role": "human", "content": "Liste 100 usuarios da minha equipe em uma tabela."},
        {"role": "assistant", "content": "100 de 141 disponíveis.", "blocos": [table.model_dump(mode="json")]}],
        ultima_rota="rh", consulta_rh=None if legacy else filters)
    own, indicators = OwnProfile(), Indicators()
    monkeypatch.setitem(subgraphs.RH_TOOLS, "buscar_meus_dados", own)
    monkeypatch.setitem(REGISTRY, "consultar_indicadores_equipe", indicators)
    profile = client.post("/chat/messages", json={"session_id": sid, "message": "Meus dados"})
    assert profile.status_code == 200 and own.calls == ["user-a"]
    assert repo.docs[sid]["consulta_rh"]["limite"] == 100
    # New worker must reconstruct context from Mongo, without old checkpoints.
    restarted_model = FakeModel()
    app.state.chat_service = ChatService(restarted_model, repository=repo, vectors=FakeVectors(), faq_vectors=FakeFaqVectors())
    response = client.post("/chat/messages", json={"session_id": sid,
        "message": "Com base nessa listagem, mostre a equipe por cargo em um gráfico de barras."})
    assert response.status_code == 200
    assert len(indicators.calls) == 1
    args, trusted = indicators.calls[0]
    assert trusted["usuario_atual"]["uid"] == "user-a" and trusted["referencia_listagem_rh"] is True
    assert args["limite"] != 100 and args["pagina"] == 1
    if not legacy:
        assert args["cargo"] == "Analista" and args["tipos"] == ["COLABORADOR"]
    body = response.json()
    assert next(b for b in body["blocos"] if b["tipo"] == "grafico_barras")["dados"][0]["valor"] == 141
    assert "juiz" in body["agentes_chamados"] and "guardrail_saida" in body["agentes_chamados"]
    history = client.get(f"/sessions/{sid}/messages").json()
    assert history["mensagens"][-1]["blocos"] == body["blocos"]
    assert history["mensagens"][1]["blocos"][0]["linhas"] == table.model_dump(mode="json")["linhas"]
    assert "Pessoa Exemplo" not in str(restarted_model.calls)
    assert "pessoa@example.com" not in str(restarted_model.calls)


@pytest.mark.parametrize("barrier", [None, "input", "judge"])
def test_profile_pdf_followup_requeries_current_uid_and_keeps_review(chat_client, monkeypatch, barrier):
    client, model, app = chat_client
    own = OwnProfile()
    monkeypatch.setitem(subgraphs.RH_TOOLS, "buscar_meus_dados", own)
    first = client.post("/chat/messages", json={"message": "Meus dados"})
    assert first.status_code == 200
    sid = first.json()["session_id"]
    deliveries = []

    class Pdf:
        async def ainvoke(self, args, config):
            deliveries.append(args)
            assert config["configurable"]["usuario_atual"]["uid"] == "user-a"
            assert "Engenheira" in args["conteudo"]
            return {"status": "ok", "url": "https://files.example.test/profile.pdf?signature=synthetic"}

    monkeypatch.setattr(graph, "gerar_pdf", Pdf())
    model.replies["roteador"] = "Quais dados você quer usar?"
    if barrier == "input":
        model.replies["guardrail_entrada"] = json.dumps({"decisao": "bloquear", "motivo": "inseguro", "mensagem": "Bloqueado."})
    if barrier == "judge":
        model.replies["juiz"] = json.dumps({"status": "rejeitado", "motivo": "Sem comprovação.", "problemas": ["Revisar."]})
    response = client.post("/chat/messages", json={"session_id": sid, "message": "Gere um PDF com esses dados"})
    assert response.status_code == 200
    blocks = response.json()["blocos"]
    assert len(own.calls) == (1 if barrier == "input" else 2)
    if barrier:
        assert not deliveries and not any(b["tipo"] == "arquivo" for b in blocks)
    else:
        assert len(deliveries) == 1 and blocks[-1]["tipo"] == "arquivo"
        assert client.get(f"/sessions/{sid}/messages").json()["mensagens"][-1]["blocos"] == blocks
    app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid="other", role="GESTOR_WORKSPACE")
    assert client.post("/chat/messages", json={"session_id": sid, "message": "Continue"}).status_code == 404


def test_faq_followup_receives_current_session_after_restart_but_not_another_session(chat_client):
    client, model, app = chat_client
    model.route = "faq"
    first = client.post("/chat/messages", json={"message": "Qual é a política interna de ergonomia?"}).json()
    # A different session of the same owner must not enter active-session context.
    prepare(app.state.chat_service.repository, [{"role": "human", "content": "MARCADOR_SESSAO_SEPARADA"},
        {"role": "assistant", "content": "Assunto particular de outra sessão."}])
    restarted_model = FakeModel("faq")
    app.state.chat_service = ChatService(restarted_model, repository=app.state.chat_service.repository,
        vectors=FakeVectors(), faq_vectors=FakeFaqVectors())
    response = client.post("/chat/messages", json={"session_id": first["session_id"], "message": "Pode detalhar essas orientações?"})
    assert response.status_code == 200
    searches = app.state.chat_service.faq_vectors.calls
    assert len(searches) == 1 and "ergonomia" in searches[0] and "Pode detalhar essas orientações?" in searches[0]
    for agent in ("faq", "juiz", "guardrail_saida"):
        messages = next(m for name, m, _ in restarted_model.calls if name == agent)
        assert any("Qual é a política interna de ergonomia?" in m.content for m in messages)
        assert "MARCADOR_SESSAO_SEPARADA" not in str(messages)


def test_intervening_profile_preserves_query_for_explicit_distribution_pdf(chat_client, monkeypatch):
    client, model, app = chat_client
    app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid="user-a", role="GESTOR_WORKSPACE")
    previous = {"acao": "consultar_indicadores_equipe", "filtros": {"agrupar_por": "cargo", "cargo": "Analista"}}
    sid = prepare(app.state.chat_service.repository, [], consulta_operacional=previous)
    monkeypatch.setitem(subgraphs.RH_TOOLS, "buscar_meus_dados", OwnProfile())
    assert client.post("/chat/messages", json={"session_id": sid, "message": "Meus dados"}).status_code == 200
    assert app.state.chat_service.repository.docs[sid]["consulta_operacional"] == previous
    indicators = Indicators()
    monkeypatch.setitem(REGISTRY, "consultar_indicadores_equipe", indicators)

    class Pdf:
        async def ainvoke(self, args, config):
            return {"status": "ok", "url": "https://files.example.test/team.pdf?signature=synthetic"}

    monkeypatch.setattr(graph, "gerar_pdf", Pdf())
    response = client.post("/chat/messages", json={"session_id": sid,
        "message": "Gere um PDF da distribuicao da minha equipe por cargo usando os dados autorizados desta conversa."})
    assert response.status_code == 200 and len(indicators.calls) == 1
    assert indicators.calls[0][0]["cargo"] == "Analista"
    assert response.json()["blocos"][-1]["tipo"] == "arquivo"
