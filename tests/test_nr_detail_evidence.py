import copy
import json
from unittest.mock import AsyncMock

import pytest

from test_chat import chat_client, texto_resposta
from app.modules.chat import subgraphs


def detail():
    return {
        "status": "ok", "modo": "detalhar", "quantidade": 1,
        "origem_principal": "web_oficial",
        "fonte": {"url": "https://www.gov.br/nr-1"},
        "fontes": [{"tipo": "web", "titulo": "MTE", "url": "https://www.gov.br/nr-1"}],
        "nrs": [{
            "numero": 1, "nome": "Disposições gerais", "situacao": "VIGENTE",
            "resumo_oficial": "Regras gerais de segurança e saúde no trabalho.",
            "objetivo": "Prevenir riscos ocupacionais.", "descricao": "Gerenciamento de riscos.",
            "aplicabilidade": "Conforme o campo de aplicação da norma.",
            "revogada": False, "tempo_reciclagem_meses": None,
            "usabilidade": "EMPRESA", "fonte_oficial": "https://www.gov.br/nr-1",
            "data_criacao": "2020-01-01", "ultima_atualizacao": "2026-01-01",
            "campo_privado_nao_renderizado": "não enviar",
        }],
    }


@pytest.mark.parametrize("mode", ["listar", "detalhar"])
def test_evidence_contains_actual_rendered_facts_without_unrelated_metadata(mode):
    result = detail()
    result["modo"] = mode
    original = copy.deepcopy(result)
    evidence = subgraphs._evidencia_compacta_nrs(result)
    assert evidence["nrs"][0]["situacao"] == "VIGENTE"
    assert evidence["nrs"][0]["numero"] == 1
    if mode == "detalhar":
        for field in ("resumo_oficial", "objetivo", "descricao", "aplicabilidade", "revogada", "usabilidade", "fonte_oficial"):
            assert evidence["nrs"][0][field] == result["nrs"][0][field]
        assert evidence["nrs"][0]["tempo_reciclagem_meses"] is None
    else:
        assert set(evidence["nrs"][0]) == {"numero", "nome", "situacao"}
    for field in ("data_criacao", "ultima_atualizacao", "campo_privado_nao_renderizado"):
        assert field not in evidence["nrs"][0]
    assert result == original


@pytest.mark.parametrize("judge_ok", [True, False])
def test_pending_then_nr_details_requeries_and_preserves_judge_and_history(chat_client, monkeypatch, judge_ok):
    client, model, app = chat_client
    model.route = "sst"
    pending = AsyncMock()
    pending.ainvoke.return_value = {
        "status": "ok", "cargo": "Técnico", "unidade": "Matriz", "quantidade": 1,
        "nrs": [{"numero": 1, "titulo": "Disposições gerais", "situacao": "PENDENTE",
                 "acao_necessaria": "CONCLUIR_PENDENCIA", "data_validade": None}],
    }
    monkeypatch.setitem(subgraphs.SST_TOOLS, "consultar_situacao_nrs", pending)
    first = client.post("/chat/messages", json={"message": "Tenho pendências de NR?"}).json()
    tool = AsyncMock()
    tool.ainvoke.return_value = detail()
    monkeypatch.setitem(subgraphs.SST_TOOLS, "consultar_nrs", tool)
    model.replies["sst"] = json.dumps({"acao": "consultar_nrs", "filtros": {"numeros": [1], "modo": "detalhar"}, "resposta": None})
    complete = model.complete

    async def review(agent, messages, **kwargs):
        if agent == "juiz":
            payload = json.loads(messages[-1].content.split("\n", 1)[1])
            evidence = payload["resultado"]["evidencia_tool"]["resultado"]
            assert evidence["nrs"][0]["objetivo"] == "Prevenir riscos ocupacionais."
            assert evidence["nrs"][0]["situacao"] == "VIGENTE"
            return json.dumps({"status": "aprovado" if judge_ok else "rejeitado",
                               "motivo": "Resultado da revisão simulada.",
                               "problemas": [] if judge_ok else ["Conteúdo sem suporte suficiente."]})
        return await complete(agent, messages, **kwargs)

    monkeypatch.setattr(model, "complete", review)
    response = client.post("/chat/messages", json={"message": "Quero detalhes da NR1", "session_id": first["session_id"]})
    assert response.status_code == 200
    body = response.json()
    tool.ainvoke.assert_awaited_once()
    assert tool.ainvoke.call_args.args[0]["numeros"] == [1]
    assert "juiz" in body["agentes_chamados"] and "guardrail_saida" in body["agentes_chamados"]
    if judge_ok:
        assert "Prevenir riscos ocupacionais." in texto_resposta(body)
        history = client.get(f"/sessions/{first['session_id']}/messages").json()
        assert history["mensagens"][-1]["blocos"] == body["blocos"]
    else:
        assert "Nenhum componente foi liberado" in texto_resposta(body)
        assert len(app.state.chat_service.repository.docs[first["session_id"]]["mensagens"]) == 2


@pytest.mark.parametrize("status", ["sem_dados", "indisponivel"])
def test_missing_details_are_not_replaced_by_history(status):
    result = {"status": status}
    evidence = subgraphs._evidencia_compacta_nrs(result)
    assert evidence["nrs"] == []
    assert "Disposições gerais" not in subgraphs._formatar_nrs(result)


@pytest.mark.parametrize("question", ["É sobre o que a NR 32?", "Sobre o que é a NR-32?"])
def test_nr32_subject_after_team_chart_has_independent_evidence(chat_client, monkeypatch, question):
    from app.api import auth
    from app.core.security import CurrentUser
    from app.modules.operational.tools import REGISTRY
    client, model, app = chat_client
    app.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid="user-a", role="GESTOR")
    chart_tool = AsyncMock()
    chart_tool.ainvoke.return_value = {
        "status": "ok", "titulo": "Equipe por cargo",
        "colunas": [{"chave": "categoria", "titulo": "Cargo"}, {"chave": "quantidade", "titulo": "Pessoas"}],
        "linhas": [{"categoria": "Cargo A", "quantidade": 3}],
        "total": 1, "pagina": 1, "total_paginas": 1, "fonte": "Cadastro autorizado",
        "unidade_grafico": "pessoas", "tipo_grafico": "grafico_barras",
        "grafico": [{"categoria": "Cargo A", "valor": 3}],
    }
    monkeypatch.setitem(REGISTRY, "consultar_indicadores_equipe", chart_tool)
    model.replies["roteador"] = json.dumps({"acao": "consultar_indicadores_equipe", "filtros": {}})
    first = client.post("/chat/messages", json={"message": "Mostre a distribuição da minha equipe por cargo."}).json()
    assert any(b["tipo"] == "grafico_barras" for b in first["blocos"])
    result = detail()
    result["nrs"][0].update(numero=32, nome="Norma de teste 32", fonte_oficial="https://www.gov.br/nr-32")
    result["fonte"]["url"] = "https://www.gov.br/nr-32"
    result["fontes"][0]["url"] = "https://www.gov.br/nr-32"
    tool = AsyncMock()
    tool.ainvoke.return_value = result
    monkeypatch.setitem(subgraphs.SST_TOOLS, "consultar_nrs", tool)
    model.calls.clear()
    model.replies["roteador"] = "ROUTE=sst"
    response = client.post("/chat/messages", json={"message": question, "session_id": first["session_id"]})
    assert response.status_code == 200
    body = response.json()
    tool.ainvoke.assert_awaited_once()
    assert tool.ainvoke.call_args.args[0]["numeros"] == [32]
    assert chart_tool.ainvoke.await_count == 1
    assert "NR-32" in texto_resposta(body) and "Nenhum componente foi liberado" not in texto_resposta(body)
    assert all(b["tipo"] == "texto" for b in body["blocos"])
    call = next(c for c in model.calls if c[0] == "juiz")
    evidence = json.loads(call[1][-1].content.split("\n", 1)[1])["resultado"]["evidencia_tool"]
    assert evidence["nome"] == "consultar_nrs"
    assert evidence["resultado"]["nrs"][0]["numero"] == 32
    for field in ("resumo_oficial", "situacao", "objetivo", "descricao", "aplicabilidade"):
        assert evidence["resultado"]["nrs"][0][field] == result["nrs"][0][field]
    history = client.get(f"/sessions/{first['session_id']}/messages").json()
    assert history["mensagens"][-1]["blocos"] == body["blocos"]
