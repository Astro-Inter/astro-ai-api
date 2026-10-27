import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_core.messages import HumanMessage

from app.core import config
from app.infrastructure.llm import models, openrouter, decisions
from app.modules.chat.errors import ChatError, InvalidAgentResponse


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "fake-secret")
    monkeypatch.setattr(config, "OPENROUTER_JEV_ENABLED", False)
    monkeypatch.setattr(openrouter.asyncio, "sleep", AsyncMock())
    calls, outcomes = [], []
    original = httpx.AsyncClient
    def handler(request):
        calls.append(request)
        assert request.url.host == "openrouter.ai"
        assert request.headers["authorization"] == "Bearer fake-secret"
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs))
    return calls, outcomes


def completion(content='{"ok":true}', finish="stop", **message):
    return httpx.Response(200, json={"choices": [{"finish_reason": finish,
        "message": {"content": content, **message}}], "usage": {"total_tokens": 10, "cost": 0.00001}})


@pytest.mark.parametrize("agent", ["rh", "sst", "agenda", "faq", "roteador", "resumo", "juiz",
                                     "orquestrador", "guardrail_entrada", "guardrail_saida"])
def test_all_text_agents_use_luna_and_application_retains_tools(transport, agent):
    calls, outcomes = transport
    outcomes.append(completion())
    assert asyncio.run(models.LanguageModels().complete(agent, [HumanMessage(content="JSON")], json_mode=True)) == '{"ok":true}'
    body = json.loads(calls[0].content)
    assert str(calls[0].url) == "https://openrouter.ai/api/v1/chat/completions"
    assert body["model"] == "openai/gpt-6-luna"
    assert body["response_format"] == {"type": "json_object"}
    assert body["reasoning"] == {"effort": "none"}
    assert body["provider"]["require_parameters"] is True
    assert "temperature" not in body and "tools" not in body


@pytest.mark.parametrize("content", ["", "ROUTE=r"])
def test_truncation_retries_once_with_larger_budget(transport, content):
    calls, outcomes = transport
    outcomes.extend([completion(content, "length"), completion("ROUTE=rh")])
    assert asyncio.run(models.LanguageModels().complete("roteador", [HumanMessage(content="RH")])) == "ROUTE=rh"
    first, second = [json.loads(r.content) for r in calls]
    assert first["max_tokens"] == 1600 and second["max_tokens"] == 4096
    assert first["messages"] == second["messages"]


@pytest.mark.parametrize("response", [completion(""), completion("x", "length"),
    completion("x", "tool_calls", tool_calls=[{"id": "x"}]), completion("x", "content_filter"),
    completion("x" * 16001), httpx.Response(200, json={"choices": []})])
def test_invalid_responses_never_become_content_or_tools(transport, response):
    calls, outcomes = transport
    outcomes.extend([response, response])
    with pytest.raises(InvalidAgentResponse):
        asyncio.run(models.LanguageModels().complete("juiz", []))
    assert len(calls) <= 2


@pytest.mark.parametrize("status,reason,attempts", [(400, "provider_failure", 1),
    (401, "provider_configuration", 1), (402, "insufficient_credits", 1),
    (403, "provider_configuration", 1), (404, "model_unavailable", 1),
    (429, "rate_limited", 2), (500, "provider_failure", 2), (503, "provider_failure", 2)])
def test_errors_bounded_and_redacted(transport, caplog, status, reason, attempts):
    calls, outcomes = transport
    outcomes.extend([httpx.Response(status, json={"error": {"message": "fake-secret sensitive-text"}})] * 2)
    with pytest.raises(ChatError) as error:
        asyncio.run(models.LanguageModels().complete("juiz", []))
    assert error.value.reason == reason and len(calls) == attempts
    assert "fake-secret" not in str(error.value) + caplog.text
    assert "sensitive-text" not in str(error.value) + caplog.text


def test_long_rate_limit_does_not_sleep_or_retry(transport):
    calls, outcomes = transport
    outcomes.append(httpx.Response(429, headers={"retry-after": "30"}, json={}))
    with pytest.raises(ChatError) as error:
        asyncio.run(models.LanguageModels().complete("juiz", []))
    assert error.value.retry_after == 30 and len(calls) == 1
    openrouter.asyncio.sleep.assert_not_awaited()


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("secret"), httpx.ConnectError("secret")])
def test_network_failure_can_recover(transport, failure):
    calls, outcomes = transport
    outcomes.extend([failure, completion()])
    assert asyncio.run(models.LanguageModels().complete("juiz", [])) == '{"ok":true}'
    assert len(calls) == 2


def test_missing_key_never_sends(transport, monkeypatch):
    calls, _ = transport
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    with pytest.raises(ChatError):
        asyncio.run(models.LanguageModels().complete("rh", []))
    assert not calls


def route_response(route="rh", confidence=0.98, safe=0.98):
    return httpx.Response(200, json={"answers": {
        "route": {"type": "choice", "choice": route, "confidence": confidence,
                  "probabilities": {k: 1.0 if k == route else 0 for k in decisions.ROUTE_CRITERIA}},
        "somente_rota": {"type": "noul", "noul": safe},
    }})


def test_jev_high_confidence_avoids_text_call(transport, monkeypatch):
    calls, outcomes = transport
    monkeypatch.setattr(config, "OPENROUTER_JEV_ENABLED", True)
    outcomes.append(route_response())
    assert asyncio.run(models.LanguageModels().complete("roteador", [HumanMessage(content="RH")])) == "ROUTE=rh"
    body = json.loads(calls[0].content)
    assert str(calls[0].url) == "https://openrouter.ai/api/alpha/decisions"
    assert body["model"] == "typesafe/jev-1.14"
    assert set(body["questions"]) == {"route", "somente_rota"}
    assert not body.get("messages") and "max_tokens" not in body
    assert len(calls) == 1


@pytest.mark.parametrize("decision", [route_response(confidence=0.89), route_response(safe=0.6),
    route_response(route="luna"), route_response(confidence=-0.1),
    httpx.Response(200, json={"answers": {}}), httpx.Response(404, json={}),
    httpx.Response(429, json={}), httpx.ReadTimeout("secret")])
def test_jev_uncertainty_and_failure_use_luna(transport, monkeypatch, decision):
    calls, outcomes = transport
    monkeypatch.setattr(config, "OPENROUTER_JEV_ENABLED", True)
    outcomes.extend([decision, completion("Pode esclarecer?")])
    assert asyncio.run(models.LanguageModels().complete("roteador", [])) == "Pode esclarecer?"
    assert len(calls) == 2
    assert json.loads(calls[-1].content)["model"] == models.TEXT_MODEL


def test_alpha_failure_circuit_does_not_add_calls(transport, monkeypatch):
    calls, outcomes = transport
    monkeypatch.setattr(config, "OPENROUTER_JEV_ENABLED", True)
    outcomes.extend([httpx.Response(503, json={}), completion("Pergunta?"), completion("Pergunta?")])
    backend = models.LanguageModels()
    async def scenario():
        await backend.complete("roteador", [])
        await backend.complete("roteador", [])
    asyncio.run(scenario())
    assert len(calls) == 3


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1, 1.1, "0.9"])
def test_decision_rejects_invalid_probabilities(value):
    with pytest.raises(ValueError):
        decisions.probability(value)

def test_classifier_excludes_system_prompt_and_bounds_history(monkeypatch):
    request = AsyncMock(return_value=json.loads(route_response().content))
    monkeypatch.setattr(decisions, "request", request)
    messages = [{"role": "system", "content": "internal-system"}] + [
        {"role": "user", "content": "x" * 3000} for _ in range(10)]
    assert asyncio.run(decisions.classify_route(messages)) == "rh"
    sent = request.await_args.args[1]["state"]["messages"]
    assert all(m["role"] != "system" for m in sent)
    assert sum(len(m["content"]) for m in sent) <= 8000


def test_embedded_error_is_sanitized_and_no_unknown_endpoint_can_receive_key(transport, caplog):
    calls, outcomes = transport
    outcomes.append(httpx.Response(200, json={"error": {"code": 402, "message": "fake-secret"}}))
    with pytest.raises(ChatError) as error:
        asyncio.run(models.LanguageModels().complete("juiz", []))
    assert error.value.reason == "insufficient_credits"
    with pytest.raises(ValueError):
        asyncio.run(openrouter.request("https://elsewhere.example", {"model": "x"}, stage="test"))
    assert len(calls) == 1 and "fake-secret" not in caplog.text
