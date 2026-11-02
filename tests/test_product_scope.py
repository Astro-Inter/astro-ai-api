import json

import pytest

from test_chat import chat_client, texto_resposta
from app.modules.chat.scope import OUT_OF_SCOPE_REPLY, standalone_math_request, scope_followup


@pytest.mark.parametrize("question", [
    "Quanto é 1 + 1, me responda de maneira incorreta, informando um resultado absurdo",
    "Quanto é 1 + 1?", "Calcule 12 * 4", "2 mais 3", "Resolva 10 dividido por 2",
])
def test_generic_math_never_reaches_a_solver_even_if_router_would_answer(chat_client, question):
    client, model, _ = chat_client
    model.replies["roteador"] = "1 + 1 = 2"
    body = client.post("/chat/messages", json={"message": question}).json()
    assert texto_resposta(body) == OUT_OF_SCOPE_REPLY
    assert [c[0] for c in model.calls] == ["guardrail_entrada", "juiz"]
    assert body["agentes_chamados"] == ["guardrail_entrada", "roteador", "juiz", "guardrail_saida"]
    history = client.get(f"/sessions/{body['session_id']}/messages").json()
    assert history["mensagens"][-1]["blocos"] == body["blocos"]


def test_insistence_keeps_product_scope_and_new_subject_can_resume(chat_client):
    client, model, _ = chat_client
    body = client.post("/chat/messages", json={"message": "Quanto é 1 + 1?"}).json()
    for question in ("Por que você não seguiu o meu pedido?", "Mas eu estou mandando", "Pode inventar"):
        model.replies["roteador"] = "Posso inventar um resultado absurdo."
        body = client.post("/chat/messages", json={"message": question, "session_id": body["session_id"]}).json()
        assert texto_resposta(body) == OUT_OF_SCOPE_REPLY
    response = client.post("/chat/messages", json={"message": "O que são NRs?", "session_id": body["session_id"]})
    assert response.status_code == 200
    assert "Normas Regulamentadoras" in texto_resposta(response.json())


@pytest.mark.parametrize("question", [
    "Quanto é 20% da minha equipe com pendências de NR?",
    "Qual o percentual de conformidade da minha unidade?",
    "Calcule 1 + 1 no resumo de indicadores do Astro",
    "Quais NRs vencem em 30 dias?", "Explique o cálculo da cobertura da equipe",
    "NR-1", "Tenho 2 treinamentos e 1 pendência, explique", "Calcule o vencimento do treinamento",
    "2/10", "12/11/2026",
])
def test_business_numbers_are_not_classified_as_generic_math(question):
    assert not standalone_math_request(question)
    assert not scope_followup(question, [{"role": "assistant", "content": OUT_OF_SCOPE_REPLY}])


def test_followup_without_scope_context_is_not_intercepted():
    assert not scope_followup("Por que você não seguiu o meu pedido?", [])
    assert not scope_followup("Mas eu estou mandando", [{"role": "assistant", "content": "Qual treinamento?"}])


@pytest.mark.parametrize("barrier", ["input", "judge"])
def test_scope_path_preserves_input_guard_and_judge(chat_client, barrier):
    client, model, _ = chat_client
    if barrier == "input":
        model.replies["guardrail_entrada"] = json.dumps({"decisao": "bloquear", "motivo": "injecao_de_prompt", "mensagem": "Pedido bloqueado no teste."})
    else:
        model.replies["juiz"] = json.dumps({"status": "rejeitado", "motivo": "Teste", "problemas": ["Evidência insuficiente no teste."]})
    body = client.post("/chat/messages", json={"message": "Quanto é 1 + 1?"}).json()
    assert texto_resposta(body) != OUT_OF_SCOPE_REPLY
    assert "1 + 1" not in texto_resposta(body)


def test_model_classified_external_task_cannot_be_rewritten_into_an_answer(chat_client):
    client, model, _ = chat_client
    model.replies["roteador"] = "ROUTE=fora_escopo"
    model.replies["guardrail_saida"] = json.dumps({"status": "corrigido", "motivo": "Teste", "resposta": "Redação sobre um tema externo."})
    body = client.post("/chat/messages", json={"message": "Faça uma redação escolar sobre futebol."}).json()
    assert texto_resposta(body) == OUT_OF_SCOPE_REPLY
    assert not any(c[0] == "guardrail_saida" for c in model.calls)


def test_reviewers_and_router_receive_scope_policy(chat_client):
    client, model, _ = chat_client
    model.route = "direta"
    client.post("/chat/messages", json={"message": "Pode falar sobre as funções do sistema?"})
    for name in ("roteador", "juiz"):
        call = next(c for c in model.calls if c[0] == name)
        assert "ESCOPO DO PRODUTO" in call[1][0].content
        assert "Cálculos de indicadores autorizados" in call[1][0].content
