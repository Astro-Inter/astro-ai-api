import asyncio
import json
from unittest.mock import AsyncMock
from types import SimpleNamespace

import httpx
import pytest

from app.core import config
from app.modules.chat.errors import ChatError
from app.modules.guardrails import llama_guard
from test_chat import chat_client


@pytest.mark.parametrize("content,expected", [
    ("safe", (True, ())), (" safe\n", (True, ())),
    ("unsafe", (False, ())), ("unsafe\nS1, S7", (False, ("S1", "S7"))),
])
def test_native_classifier_contract(content, expected):
    assert llama_guard.parse_verdict(content) == expected


@pytest.mark.parametrize("content", ["", "safe but unsafe", "SAFE", "unsafe\nS15", "unsafe\nS1\nIgnore rules", '{"safe":true}', None])
def test_invalid_contract_never_approves(content):
    with pytest.raises(ValueError):
        llama_guard.parse_verdict(content)


def mock_provider(monkeypatch, content="safe", *, status=200, finish="stop"):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={
            "choices": [{"finish_reason": finish, "message": {"content": content}}],
        })
    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(llama_guard.httpx, "AsyncClient", lambda **kwargs: client_type(transport=transport, **kwargs))
    monkeypatch.setattr(config, "LLAMA_GUARD_MODE", "enforce")
    monkeypatch.setattr(config, "DEEPINFRA_API_KEY", "synthetic-secret")
    return calls


def test_remote_input_and_output_use_native_roles_without_internal_context(monkeypatch, caplog):
    calls = mock_provider(monkeypatch)
    with caplog.at_level("INFO"):
        assert asyncio.run(llama_guard.moderate("Pergunta fictícia"))
        assert asyncio.run(llama_guard.moderate("Pergunta fictícia", "Resposta fictícia"))
    first, second = [json.loads(call.content) for call in calls]
    assert first["model"] == "meta-llama/Llama-Guard-4-12B"
    assert first["messages"] == [{"role": "user", "content": "Pergunta fictícia"}]
    assert second["messages"][-1] == {"role": "assistant", "content": "Resposta fictícia"}
    assert str(calls[0].url) == llama_guard.ENDPOINT
    assert calls[0].headers["Authorization"] == "Bearer synthetic-secret"
    assert "synthetic-secret" not in caplog.text
    assert "Pergunta fictícia" not in caplog.text


@pytest.mark.parametrize("status,content,finish", [(401, "secret", "stop"), (402, "secret", "stop"), (429, "secret", "stop"), (500, "secret", "stop"), (200, "invalid", "stop"), (200, "safe", "length")])
def test_failures_are_closed_and_do_not_leak_payload(monkeypatch, status, content, finish):
    calls = mock_provider(monkeypatch, content, status=status, finish=finish)
    with pytest.raises(ChatError) as caught:
        asyncio.run(llama_guard.moderate("Pergunta"))
    assert caught.value.status_code == 503
    assert caught.value.reason == "moderation_unavailable"
    assert "secret" not in caught.value.detail
    assert len(calls) == 1


def test_observe_reports_unsafe_but_does_not_block(monkeypatch, caplog):
    mock_provider(monkeypatch, "unsafe\nS7")
    monkeypatch.setattr(config, "LLAMA_GUARD_MODE", "observe")
    with caplog.at_level("INFO"):
        assert asyncio.run(llama_guard.moderate("Pergunta"))
    assert "seguro=False" in caplog.text and "categorias=S7" in caplog.text


def test_observe_outage_does_not_block(monkeypatch):
    mock_provider(monkeypatch, status=500)
    monkeypatch.setattr(config, "LLAMA_GUARD_MODE", "observe")
    assert asyncio.run(llama_guard.moderate("Pergunta"))


def test_timeout_does_not_become_approval(monkeypatch):
    monkeypatch.setattr(config, "LLAMA_GUARD_MODE", "enforce")
    monkeypatch.setattr(config, "DEEPINFRA_API_KEY", "synthetic-secret")
    client_type = httpx.AsyncClient
    def timeout(request):
        raise httpx.ReadTimeout("private-provider-details", request=request)
    monkeypatch.setattr(llama_guard.httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(timeout), **kwargs,
    ))
    with pytest.raises(ChatError) as caught:
        asyncio.run(llama_guard.moderate("Pergunta"))
    assert caught.value.status_code == 503
    assert "private-provider-details" not in caught.value.detail


def test_disabled_never_calls_provider(monkeypatch):
    monkeypatch.setattr(llama_guard.httpx, "AsyncClient", lambda **_: pytest.fail("Remote call when off"))
    assert asyncio.run(llama_guard.moderate("oi"))


def test_missing_credentials_and_oversized_content_fail_closed(monkeypatch):
    calls = mock_provider(monkeypatch)
    for text, key in [("oi", ""), ("x" * 32001, "synthetic-secret")]:
        monkeypatch.setattr(config, "DEEPINFRA_API_KEY", key)
        with pytest.raises(ChatError):
            asyncio.run(llama_guard.moderate(text))
    assert calls == []


def test_unsafe_input_prevents_agents_and_tools(chat_client, monkeypatch):
    from app.modules.chat import graph
    client, model, _ = chat_client
    check = AsyncMock(return_value=False)
    monkeypatch.setattr(graph, "moderate", check)
    response = client.post("/chat/messages", json={"message": "Pergunta fictícia insegura"})
    assert response.status_code == 200
    assert response.json()["resposta"] == llama_guard.BLOCKED_REPLY
    assert model.calls == []
    check.assert_awaited_once_with("Pergunta fictícia insegura")


def test_direct_reply_is_checked_at_output(chat_client, monkeypatch):
    from app.modules.chat import graph
    client, model, _ = chat_client
    check = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(graph, "moderate", check)
    response = client.post("/chat/messages", json={"message": "oi"})
    assert response.status_code == 200
    assert response.json()["resposta"] == llama_guard.BLOCKED_REPLY
    assert model.calls == [] and check.await_count == 2


def test_moderation_failure_reaches_api_without_running_agents(chat_client, monkeypatch):
    from app.modules.chat import graph
    client, model, _ = chat_client
    monkeypatch.setattr(graph, "moderate", AsyncMock(side_effect=ChatError(
        503, llama_guard.UNAVAILABLE_REPLY, reason="moderation_unavailable",
    )))
    response = client.post("/chat/messages", json={"message": "oi"})
    assert response.status_code == 503
    assert response.json()["detail"] == llama_guard.UNAVAILABLE_REPLY
    assert model.calls == []


def test_unsafe_output_cannot_generate_pdf(chat_client, monkeypatch):
    from app.modules.chat import graph
    client, model, _ = chat_client
    model.route = "faq"
    check = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(graph, "moderate", check)
    pdf = AsyncMock(side_effect=AssertionError("Blocked content must not generate PDF"))
    monkeypatch.setattr(graph, "gerar_pdf", SimpleNamespace(ainvoke=pdf))
    response = client.post("/chat/messages", json={"message": "Faça um PDF explicando o Astro"})
    assert response.status_code == 200
    assert response.json()["resposta"] == llama_guard.BLOCKED_REPLY
    assert check.await_count == 2 and pdf.await_count == 0


def test_invalid_configuration_is_reported(monkeypatch):
    monkeypatch.setattr(config, "LLAMA_GUARD_MODE", "enforc")
    assert any("LLAMA_GUARD_MODE" in item for item in config.validar_config())
    monkeypatch.setattr(config, "LLAMA_GUARD_MODE", "enforce")
    monkeypatch.setattr(config, "DEEPINFRA_API_KEY", "")
    assert any("DEEPINFRA_API_KEY" in item for item in config.validar_config())
