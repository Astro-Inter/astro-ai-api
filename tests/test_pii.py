"""Confere o payload enviado à IA e a restauração local para uso autorizado."""

import asyncio
import json
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage
from langsmith import tracing_context

from app.core.pii import PiiTokenizer, redact_for_embedding, redact_trace_data
from app.core.security import CurrentUser
from app.modules.chat.agents import invoke_agent
from app.modules.chat.schemas import ChatRequest, JudgeDecision
from app.modules.memory.service import ConversationMemory
from app.observability import privacy as trace_privacy
from memory_fakes import FakeSessions, FakeVectors


TOKEN = re.compile(r"\[ASTRO_PII_[0-9a-f]{12}_[A-Z]+_\d+\]")


def test_identifiers_are_consistent_across_text_and_structured_evidence():
    privacy = PiiTokenizer()
    person = {"nome": 'Ana "Lima"', "email": "ana@privado.example", "cpf": "123.456.789-01"}
    privacy.seed(person)
    masked = privacy.mask_data(person)
    assert privacy.mask_text(person["email"]) == masked["email"]
    assert privacy.mask_text(person["cpf"]) == masked["cpf"]
    assert privacy.mask_text("12345678901") == masked["cpf"]
    assert privacy.mask_text(person["nome"]) == masked["nome"]
    # Aspas de nomes restaurados não alteram o contrato JSON.
    restored = json.loads(privacy.restore_output(json.dumps(masked)))
    assert restored == person
    assert person["email"] not in repr(privacy)


def test_context_keeps_business_data_and_removes_authentication_identifiers():
    privacy = PiiTokenizer()
    data = {"usuario_atual": {"uid": "firebase-private", "role": "GESTOR"},
            "session_id": str(uuid4()), "cargo": {"nome": "Soldadora"},
            "nr": {"nome": "Segurança em máquinas", "numero": 12},
            "data": "20/10/2026", "horario": "10:30", "quantidade": 4}
    privacy.seed(data)
    masked = privacy.mask_data(data)
    assert masked["usuario_atual"] == {"uid": "[IDENTIFICADOR_REMOVIDO]", "role": "GESTOR"}
    assert masked["session_id"] == "[IDENTIFICADOR_REMOVIDO]"
    for key in ("cargo", "nr", "data", "horario", "quantidade"):
        assert masked[key] == data[key]


@pytest.mark.parametrize("value", ["123.456.789-01", "12345678901", "12.345.678/0001-90",
                                   "ana@privado.example", "(11) 99999-8888", "CEP 01234-567",
                                   "Me chamo Ana Lima", "Nome: Ana Lima"])
def test_patterns_mask_personal_data_and_restore_only_with_local_map(value):
    privacy = PiiTokenizer()
    masked = privacy.mask_text(value)
    assert TOKEN.search(masked)
    assert privacy.restore_output(masked) == value
    other = PiiTokenizer()
    with pytest.raises(ValueError):
        other.restore_output(masked)


def test_secrets_are_removed_without_reversible_mapping():
    privacy = PiiTokenizer()
    text = "Bearer abc.def.private; senha=segredo-pessoal"
    masked = privacy.mask_text(text)
    assert "abc.def.private" not in masked and "segredo-pessoal" not in masked
    assert privacy.restore_output(masked) == masked
    assert not privacy.has_tokens
    structured = privacy.mask_text('{"password": "segredo com espaços"}')
    assert "segredo com espaços" not in structured
    assert json.loads(structured)["password"] == "[SEGREDO_REMOVIDO]"


def test_router_command_restores_recipient_without_changing_json_structure():
    async def scenario():
        calls = []

        class Model:
            async def complete(self, name, messages, **kwargs):
                calls.append(messages)
                token = TOKEN.search(messages[-1].content).group()
                return 'CONVERSATION=' + json.dumps({"pessoa": token})

        state = {"usuario_atual": CurrentUser(uid="private-firebase", role="GESTOR"),
                 "contexto": {}, "historico": [], "mensagem": "Consulte conversas com ana@privado.example."}
        reply = await invoke_agent(Model(), "roteador", "Escolha uma ferramenta autorizada.", state)
        assert json.loads(reply.split("=", 1)[1])["pessoa"] == "ana@privado.example"
        assert "ana@privado.example" not in str(calls)
        assert "private-firebase" not in str(calls)
        assert "GESTOR" in str(calls)
        assert "ana@privado.example" in state["mensagem"]

    asyncio.run(scenario())


def test_judge_sees_masked_history_memory_tool_result_and_candidate():
    async def scenario():
        calls = []

        class Model:
            async def complete(self, name, messages, **kwargs):
                calls.append(messages)
                return json.dumps({"status": "aprovado", "motivo": "Evidência compatível.", "problemas": []})

        person = {"nome": "Ana Lima", "email": "ana@privado.example", "cpf": "123.456.789-01"}
        state = {"usuario_atual": CurrentUser(uid="private-firebase", role="GESTOR"), "contexto": {},
                 "historico": [{"role": "user", "content": "Meu e-mail é ana@privado.example"}],
                 "mensagem": "Confira o CPF 123.456.789-01", "memoria_consultada": True,
                 "memoria": {"conversas": [{"session_id": "private-session", "resumo": "CPF 123.456.789-01"}]},
                 "resultado_tool": {"dados": person},
                 "resultado": {"dominio": "rh", "evidencia_tool": {"resultado": {"dados": person}}},
                 "candidato": "Ana Lima: ana@privado.example. CPF 123.456.789-01."}
        decision = await invoke_agent(Model(), "juiz", "Compare com a evidência.", state, JudgeDecision)
        assert decision.status == "aprovado"
        sent = str(calls)
        for private in (*person.values(), "private-session", "private-firebase"):
            assert private not in sent
        assert "GESTOR" in sent
        assert state["resultado_tool"]["dados"] == person

    asyncio.run(scenario())


def test_manual_summary_masks_input_but_saves_restored_summary_locally():
    async def scenario():
        repository = FakeSessions()
        sid = str(uuid4())
        await repository.ensure(sid, "owner")
        doc, token = await repository.acquire(sid, "owner")
        doc["mensagens"] = [{"role": "human", "content": "Meu email é ana@privado.example"}]
        calls = []

        class Model:
            async def complete(self, name, messages, **kwargs):
                calls.append(messages)
                email = TOKEN.search(messages[-1].content).group()
                return json.dumps({"resumo": f"O usuário informou o contato {email}."})

        summary = await ConversationMemory(repository, FakeVectors(), Model()).summarize(doc, token)
        assert "ana@privado.example" not in str(calls)
        assert "ana@privado.example" in summary
        assert repository.docs[sid]["resumo_parcial"] == summary
        assert "ASTRO_PII" not in summary

    asyncio.run(scenario())


def test_embedding_redaction_is_stable_and_does_not_change_dates_or_nr_numbers():
    value = "CPF 123.456.789-01; contato ana@privado.example; NR-12 em 20/10/2026 às 10:30"
    first = redact_for_embedding(value)
    assert redact_for_embedding(value) == first
    assert "123.456.789-01" not in first and "ana@privado.example" not in first
    assert "NR-12 em 20/10/2026 às 10:30" in first


def test_trace_export_omits_contents_but_preserves_token_and_resolution_metrics(monkeypatch):
    received = {}

    def factory(**kwargs):
        received.update(kwargs)
        return SimpleNamespace()

    trace_privacy.private_langsmith_client.cache_clear()
    monkeypatch.setattr(trace_privacy, "Client", factory)
    try:
        client = trace_privacy.private_langsmith_client()
        assert received["hide_inputs"] is True
        safe = received["hide_outputs"]({
            "messages": [{"content": "Nome: Ana Lima, ana@privado.example"}],
            "resultado_tool": {"cpf": "123.456.789-01"}, "resolved": 1,
            "llm_output": {"token_usage": {"prompt_tokens": 12, "completion_tokens": 8}},
        })
        assert "Ana Lima" not in str(safe) and "123.456.789-01" not in str(safe)
        assert safe["resolved"] == 1
        assert safe["llm_output"]["token_usage"]["prompt_tokens"] == 12
        redacted = redact_trace_data({"uid": "private-id", "email": "ana@privado.example"})
        assert "private-id" not in str(redacted) and "ana@privado.example" not in str(redacted)
        monkeypatch.setattr(trace_privacy.config, "LANGSMITH_API_KEY", "test-only")
        with tracing_context(enabled=True), trace_privacy.private_tracing():
            from langchain_core.tracers.context import tracing_v2_callback_var
            assert tracing_v2_callback_var.get().client is client
    finally:
        trace_privacy.private_langsmith_client.cache_clear()
