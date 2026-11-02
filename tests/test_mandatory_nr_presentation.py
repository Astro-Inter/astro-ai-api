import copy
import json
from unittest.mock import AsyncMock

import pytest

from test_chat import chat_client, texto_resposta
from app.modules.chat import subgraphs


def assigned_nrs():
    return {"status": "ok", "cargo": "Técnico", "unidade": "Matriz", "quantidade": 2,
            "nrs": [{"numero": 1, "titulo": "Norma de teste 1", "tempo_reciclagem_meses": 12},
                    {"numero": 5, "titulo": "Norma de teste 5", "tempo_reciclagem_meses": 12}]}


def test_assignment_list_does_not_claim_personal_recycling_or_validity():
    result = assigned_nrs()
    original = copy.deepcopy(result)
    text = subgraphs._formatar_nrs_obrigatorias(result)
    assert "NR-1" in text and "NR-5" in text
    assert "vinculada(s) no Astro" in text
    assert "não confirma conclusão ou validade" in text
    for word in ("vigente", "reciclagem", "12 meses", "em dia"):
        assert word not in text
    assert result == original
    assert subgraphs._evidencia_nrs_obrigatorias(result)["nrs"] == result["nrs"]


@pytest.mark.parametrize("judge_ok", [True, False])
def test_which_nrs_must_i_fulfill_queries_authenticated_assignments(chat_client, monkeypatch, judge_ok):
    client, model, app = chat_client
    model.route = "sst"
    tool = AsyncMock()
    tool.ainvoke.return_value = assigned_nrs()
    monkeypatch.setitem(subgraphs.SST_TOOLS, "consultar_nrs_obrigatorias", tool)
    if not judge_ok:
        model.replies["juiz"] = json.dumps({"status": "rejeitado", "motivo": "Teste de controle", "problemas": ["Inconsistência simulada"]})
    response = client.post("/chat/messages", json={"message": "Quais NRs preciso cumprir?"})
    assert response.status_code == 200
    body = response.json()
    tool.ainvoke.assert_awaited_once()
    assert tool.ainvoke.call_args.args == ({},)
    assert tool.ainvoke.call_args.kwargs["config"]["configurable"]["usuario_atual"]["uid"] == "user-a"
    assert "juiz" in body["agentes_chamados"] and "guardrail_saida" in body["agentes_chamados"]
    if judge_ok:
        assert "NR-1" in texto_resposta(body) and "NR-5" in texto_resposta(body)
        history = client.get(f"/sessions/{body['session_id']}/messages").json()
        assert history["mensagens"][-1]["blocos"] == body["blocos"]
    else:
        assert "Não consegui confirmar uma resposta confiável" in texto_resposta(body)


def test_no_assignments_does_not_become_no_legal_obligations():
    result = {**assigned_nrs(), "nrs": [], "quantidade": 0}
    text = subgraphs._formatar_nrs_obrigatorias(result)
    assert "Não encontrei NRs vinculadas ao seu cargo nessa consulta." in text
    assert "nenhuma obrigação" not in text and "em dia" not in text
