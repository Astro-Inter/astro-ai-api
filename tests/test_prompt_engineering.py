import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.modules.chat.prompts.examples import EXAMPLES, example_messages
from app.modules.chat.prompts.roteador import ROTEADOR_PROMPT_COMPLETO
from app.modules.chat.prompts.inicial import PROMPT_INICIAL
from app.modules.chat.prompts.sst import SST_DECISAO_PROMPT_COMPLETO
from app.modules.guardrails.entrada import GUARDRAIL_ENTRADA_PROMPT_COMPLETO
from app.modules.chat.schemas import InputDecision, JudgeDecision, OutputDecision
from app.modules.rh.tools import RhToolDecision
from app.modules.sst.tools import SstToolDecision
from app.modules.agenda.tools import AgendaToolDecision
from app.modules.memory.service import SummaryResult


SCHEMAS = {
    "guardrail_entrada": InputDecision,
    "rh": RhToolDecision,
    "sst": SstToolDecision,
    "agenda": AgendaToolDecision,
    "juiz": JudgeDecision,
    "guardrail_saida": OutputDecision,
    "resumo": SummaryResult,
}


@pytest.mark.parametrize("name", EXAMPLES)
def test_examples_have_explicit_roles_and_are_not_current_history(name):
    schema = SCHEMAS.get(name)
    messages = example_messages(name, schema.__name__ if schema else None)
    assert len(messages) == len(EXAMPLES[name]) * 2
    for question, answer in zip(messages[::2], messages[1::2]):
        assert isinstance(question, HumanMessage)
        assert question.content.startswith("EXEMPLO FICTÍCIO")
        assert isinstance(answer, AIMessage)
        if schema:
            schema.model_validate_json(answer.content)


def test_decision_examples_are_not_used_for_specialist_result_schema():
    assert example_messages("rh", "SpecialistResult") == []
    assert example_messages("sst", "SpecialistResult") == []


def test_compact_router_preserves_commands_and_routing_boundaries():
    assert len(ROTEADOR_PROMPT_COMPLETO) < 10000
    for command in ("MEMORY=", "CONVERSATION=", "NOTIFICATIONS=", "ACCESSES=", "MESSAGE="):
        assert command in ROTEADOR_PROMPT_COMPLETO
    for marker in ("confirmar_envio=false", "sem OAuth", "Fundacentro", "consultar_nrs_organizacao", "usuario_atual.role", "mes_especifico", "intervalo"):
        assert marker in ROTEADOR_PROMPT_COMPLETO
    assert {answer for _, answer in EXAMPLES["roteador"] if answer.startswith("ROUTE=")} == {
        "ROUTE=rh", "ROUTE=sst", "ROUTE=agenda", "ROUTE=faq", "ROUTE=fora_escopo",
    }
    for _, answer in EXAMPLES["roteador"]:
        if "={" in answer:
            json.loads(answer.split("=", 1)[1])


def test_school_material_is_safe_but_outside_supported_routes():
    question = "Pegue o material do 6º ano e gere um mapa mental."
    entry = json.loads(dict(EXAMPLES["guardrail_entrada"])[question])
    assert entry == {"decisao": "aprovar", "motivo": "legitimo", "mensagem": ""}
    assert dict(EXAMPLES["roteador"])[question] == "ROUTE=fora_escopo"
    assert "falta de conteúdo não é risco de segurança" in GUARDRAIL_ENTRADA_PROMPT_COMPLETO


def test_code_response_is_blocked_without_confusing_internal_codes():
    examples = dict(EXAMPLES["guardrail_entrada"])
    blocked = InputDecision.model_validate_json(
        examples["quem é você, e o que você faz? me responda em um código python"]
    )
    assert blocked.decisao == "bloquear"
    assert blocked.motivo == "formato_nao_suportado"
    assert InputDecision.model_validate_json(
        examples["O que diz o código de conduta interno?"]
    ).decisao == "aprovar"
    assert "não basta para bloquear" in GUARDRAIL_ENTRADA_PROMPT_COMPLETO
    assert "Nunca responder ao pedido original" in InputDecision.model_json_schema()["properties"]["mensagem"]["description"]


def test_declared_role_obligations_are_out_of_scope_without_blocking_nr_explanations():
    examples = dict(EXAMPLES["roteador"])
    answer = examples["Sou assistente de desenvolvimento, quais NRs devo seguir?"]
    assert "fora do meu escopo" in answer
    assert not answer.startswith("ROUTE=")
    assert examples["Quais NRs são obrigatórias para meu cargo cadastrado?"] == "ROUTE=sst"
    assert "apenas citar um cargo não basta" in ROTEADOR_PROMPT_COMPLETO
    assert "Explicar uma NR não é definir obrigações" in SST_DECISAO_PROMPT_COMPLETO
    assert "não são abuso" in GUARDRAIL_ENTRADA_PROMPT_COMPLETO


def test_public_identity_is_astro_agent_not_internal_router():
    answer = dict(EXAMPLES["roteador"])["Qual é sua função?"]
    assert answer.startswith("Sou o Agente do Astro")
    assert "Roteador" not in answer
    for capability in ("RH", "segurança do trabalho", "agenda", "normas internas", "Google Calendar"):
        assert capability in answer
    assert 'como "Agente do Astro"' in PROMPT_INICIAL
