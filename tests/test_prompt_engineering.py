import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.modules.chat.prompts.examples import EXAMPLES, example_messages
from app.modules.chat.prompts.roteador import ROTEADOR_PROMPT_COMPLETO
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
        "ROUTE=rh", "ROUTE=sst", "ROUTE=agenda", "ROUTE=faq",
    }
    for _, answer in EXAMPLES["roteador"]:
        if "={" in answer:
            json.loads(answer.split("=", 1)[1])
