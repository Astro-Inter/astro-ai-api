import json

import pytest

from test_chat import chat_client, texto_resposta
from app.api import auth
from app.core.security import CurrentUser
from app.modules.chat.chart_menu import make_chart_menu, select_chart, expiry_followup
from app.modules.chat import subgraphs
from app.modules.operational.tools import OperationalDecision, REGISTRY


def manager(app, role="GESTOR"):
    app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid="user-a", role=role)


def offer(client):
    response = client.post("/chat/messages", json={"message": "quais graficos vc pode fazer?"})
    assert response.status_code == 200
    return response.json()


@pytest.mark.parametrize("role,count", [("GESTOR", 6), ("GESTOR_WORKSPACE", 6), ("COLABORADOR", 2), ("ADMIN", 0)])
def test_numbered_catalog_matches_current_role(role, count):
    text, menu = make_chart_menu(role)
    assert len(menu["opcoes"]) == count if menu else count == 0
    assert "Como gestor" not in text
    if menu:
        assert "1. " in text and "linhas" in text and "barras" in text


@pytest.mark.parametrize("choice", ["1", "opção 1", "pendências", "faca isso, quero um grafico das pendencias"])
@pytest.mark.parametrize("barrier", [None, "judge", "unavailable"])
def test_menu_selection_executes_tool_without_reconfirmation(chat_client, monkeypatch, choice, barrier):
    client, model, app = chat_client
    manager(app)
    calls = []

    class Tool:
        async def ainvoke(self, args, config):
            calls.append((args, config))
            if barrier == "unavailable":
                return {"status": "indisponivel", "mensagem": "Consulta temporariamente indisponível."}
            return {"status": "ok", "titulo": "Pendências por NR", "fonte": "Consulta sintética autorizada",
                    "colunas": [{"chave": "categoria", "titulo": "NR"}, {"chave": "quantidade", "titulo": "Pendências"}],
                    "linhas": [{"categoria": "NR-1", "quantidade": 2}], "total": 1,
                    "pagina": 1, "total_paginas": 1, "unidade_grafico": "pendências",
                    "tipo_grafico": args["tipo_grafico"], "grafico": [{"categoria": "NR-1", "valor": 2}]}

    monkeypatch.setitem(REGISTRY, "consultar_grafico_pendencias_nr", Tool())
    first = offer(client)
    doc = app.state.chat_service.repository.docs[first["session_id"]]
    assert doc["menu_graficos"]["opcoes"][0] == "pendencias_nr"
    model.replies["roteador"] = "Ainda não consultei."
    if barrier == "judge":
        model.replies["juiz"] = json.dumps({"status": "rejeitado", "motivo": "Teste", "problemas": ["Dados inválidos."]})
    body = client.post("/chat/messages", json={"message": choice, "session_id": first["session_id"]}).json()
    assert len(calls) == 1 and calls[0][1]["configurable"]["usuario_atual"]["uid"] == "user-a"
    assert not any(c[0] == "roteador" for c in model.calls)
    assert "juiz" in body["agentes_chamados"]
    assert any(b["tipo"] == "grafico_barras" for b in body["blocos"]) == (barrier is None)
    assert "Ainda não consultei" not in texto_resposta(body)
    history = client.get(f"/sessions/{first['session_id']}/messages").json()
    if barrier == "judge":
        assert history["mensagens"][-1]["blocos"] == first["blocos"]
    else:
        assert history["mensagens"][-1]["blocos"] == body["blocos"]


def test_personal_numeric_choice_uses_authenticated_monthly_series(chat_client, monkeypatch):
    client, model, app = chat_client
    calls = []

    class Tool:
        async def ainvoke(self, args, config):
            calls.append(config["configurable"]["usuario_atual"]["uid"])
            return {"status": "ok", "categorias": {f"2026-{month:02d}": 0 for month in range(1, 13)}, "fonte": "Consulta pessoal sintética"}

    monkeypatch.setitem(subgraphs.AGENDA_TOOLS, "consultar_evolucao_treinamentos", Tool())
    first = offer(client)
    body = client.post("/chat/messages", json={"message": "1", "session_id": first["session_id"]}).json()
    chart = next(b for b in body["blocos"] if b["tipo"] == "grafico_linhas")
    assert len(chart["dados"]) == 12 and all(row["valor"] == 0 for row in chart["dados"])
    assert calls == ["user-a"] and not any(c[0] in {"roteador", "agenda"} for c in model.calls)


@pytest.mark.parametrize("choice", ["s", "sim", "0", "99"])
def test_unselected_confirmation_or_invalid_number_keeps_menu(chat_client, choice):
    client, _, app = chat_client
    manager(app)
    first = offer(client)
    body = client.post("/chat/messages", json={"message": choice, "session_id": first["session_id"]}).json()
    assert "Escolha uma opção" in texto_resposta(body)
    assert app.state.chat_service.repository.docs[first["session_id"]]["menu_graficos"]


def test_menu_cannot_be_reused_in_another_session_or_after_role_change(chat_client):
    client, model, app = chat_client
    manager(app)
    first = offer(client)
    model.route = "direta"
    other = client.post("/chat/messages", json={"message": "1"}).json()
    assert other["session_id"] != first["session_id"]
    assert not app.state.chat_service.repository.docs[other["session_id"]].get("menu_graficos")
    manager(app, "COLABORADOR")
    body = client.post("/chat/messages", json={"message": "1", "session_id": first["session_id"]}).json()
    assert "não está disponível" in texto_resposta(body)
    app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid="user-b", role="GESTOR")
    assert client.post("/chat/messages", json={"message": "1", "session_id": first["session_id"]}).status_code == 404


def test_unrelated_approved_turn_clears_menu_and_rejected_offer_is_not_saved(chat_client):
    client, model, app = chat_client
    first = offer(client)
    model.route = "direta"
    client.post("/chat/messages", json={"message": "Olá", "session_id": first["session_id"]})
    assert app.state.chat_service.repository.docs[first["session_id"]]["menu_graficos"] is None
    model.replies["juiz"] = json.dumps({"status": "rejeitado", "motivo": "Teste", "problemas": ["Teste."]})
    rejected = offer(client)
    assert not app.state.chat_service.repository.docs[rejected["session_id"]].get("menu_graficos")


@pytest.mark.parametrize("days", ["30 d", "30 dias", "nos próximos 30 dias"])
def test_expiry_period_followup_calls_query_with_validated_days(chat_client, monkeypatch, days):
    client, model, app = chat_client
    manager(app)
    calls = []

    class Tool:
        async def ainvoke(self, args, config):
            calls.append((args, config))
            return {"status": "sem_dados", "mensagem": "Nenhum vencimento encontrado nesse período."}

    monkeypatch.setitem(REGISTRY, "consultar_vencimentos_conformidades", Tool())
    first = client.post("/chat/messages", json={"message": "venximento"}).json()
    assert "qual período" in texto_resposta(first)
    body = client.post("/chat/messages", json={"message": days, "session_id": first["session_id"]}).json()
    assert len(calls) == 1 and calls[0][0]["dias"] == 30
    assert "Nenhum vencimento" in texto_resposta(body)
    assert not any(c[0] == "roteador" for c in model.calls)
    assert app.state.chat_service.repository.docs[first["session_id"]]["consulta_prazo"] is False


def test_deadline_and_malformed_menu_do_not_authorize_arbitrary_requests():
    assert expiry_followup("30 d", False, "GESTOR") is None
    assert expiry_followup("30 d", True, "COLABORADOR") is None
    assert expiry_followup("30 d de outra empresa", True, "GESTOR") is None
    assert isinstance(expiry_followup("999 d", True, "GESTOR")[0], str)
    assert isinstance(select_chart("1", {"versao": 1, "opcoes": ["unknown"]}, "GESTOR"), str)
    assert select_chart("1", None, "GESTOR") is None
    assert isinstance(select_chart("venximento", make_chart_menu("GESTOR")[1], "GESTOR"), OperationalDecision)


@pytest.mark.parametrize("number,action", [(1, "consultar_grafico_pendencias_nr"), (2, "consultar_grafico_vencimentos"),
    (3, "consultar_indicadores_cobertura"), (4, "consultar_indicadores_equipe")])
def test_each_team_choice_has_valid_filters(number, action):
    decision = select_chart(str(number), make_chart_menu("GESTOR")[1], "GESTOR")
    assert isinstance(decision, OperationalDecision) and decision.acao == action
    assert decision.filtros.tipo_grafico == "grafico_barras"
    assert decision.filtros.pagina == 1 and decision.filtros.limite == 50


def test_input_guard_can_block_menu_before_routing(chat_client):
    client, model, app = chat_client
    model.replies["guardrail_entrada"] = json.dumps({"decisao": "bloquear", "motivo": "injecao_de_prompt", "mensagem": "Pedido bloqueado no teste."})
    first = offer(client)
    assert not app.state.chat_service.repository.docs[first["session_id"]].get("menu_graficos")
    assert [call[0] for call in model.calls] == ["guardrail_entrada"]
