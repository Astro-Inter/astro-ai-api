import asyncio
import json
from types import SimpleNamespace

import pytest
import httpx
from langchain_core.messages import AIMessage, HumanMessage

from app.core import config
from app.infrastructure.llm import models
from app.modules.chat.errors import ChatError, InvalidAgentResponse


@pytest.mark.parametrize("truncated_content", ["", "ROUTE=r"])
@pytest.mark.parametrize("json_mode", [False, True])
def test_truncated_response_retries_once_preserving_request(truncated_content, json_mode):
    class TruncatedModel:
        def __init__(self):
            self.bindings = []
            self.calls = []

        def bind(self, **kwargs):
            self.bindings.append(kwargs)
            return self

        async def ainvoke(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            if len(self.calls) == 1:
                return AIMessage(content=truncated_content, response_metadata={"finish_reason": "length"})
            return AIMessage(content="ROUTE=fora_escopo", response_metadata={"finish_reason": "stop"})

    model = TruncatedModel()
    messages = [HumanMessage(content="pegue o material do 6º ano e gere um mapa mental")]
    assert asyncio.run(models.LanguageModels._invoke(model, "roteador", messages, json_mode)) == "ROUTE=fora_escopo"
    assert model.calls == [(messages, {"config": {"run_name": "roteador"}})] * 2
    assert model.bindings == (
        [{"response_format": {"type": "json_object"}}] if json_mode else []
    ) + [{"max_tokens": models.TRUNCATION_RETRY_MAX_TOKENS}]


@pytest.mark.parametrize("finish_reason,expected_calls", [("length", 2), ("stop", 1)])
def test_empty_response_retries_only_for_token_limit_and_remains_bounded(finish_reason, expected_calls):
    class EmptyModel:
        calls = 0

        def bind(self, **kwargs):
            return self

        async def ainvoke(self, *_args, **_kwargs):
            self.calls += 1
            return AIMessage(content="", response_metadata={"finish_reason": finish_reason})

    model = EmptyModel()
    with pytest.raises(InvalidAgentResponse):
        asyncio.run(models.LanguageModels._invoke(model, "roteador", [], False))
    assert model.calls == expected_calls


class ProviderError(Exception):
    def __init__(self, status, retry_after="30"):
        self.status_code = status
        self.response = SimpleNamespace(headers={"retry-after": retry_after})
        super().__init__("secret-provider-payload")


def test_real_groq_sdk_increases_budget_after_empty_truncated_response(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "fake-key")
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "")
    requests = []

    async def send(client, request, **kwargs):
        requests.append(json.loads(request.content))
        first = len(requests) == 1
        return httpx.Response(200, request=request, json={
            "id": "test-completion", "object": "chat.completion", "created": 1,
            "model": models.GROQ_FAST_MODEL,
            "choices": [{"index": 0, "finish_reason": "length" if first else "stop", "message": {
                "role": "assistant", "content": "" if first else '{"ok":true}',
            }}],
        })

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    models.get_model.cache_clear()
    try:
        assert asyncio.run(models.LanguageModels().complete(
            "roteador", [HumanMessage(content="Teste")], json_mode=True,
        )) == '{"ok":true}'
        assert len(requests) == 2
        assert requests[0]["max_tokens"] == 1600
        assert requests[1]["max_tokens"] == models.TRUNCATION_RETRY_MAX_TOKENS
        assert requests[0]["messages"] == requests[1]["messages"]
        assert requests[0]["response_format"] == requests[1]["response_format"] == {"type": "json_object"}
    finally:
        models.get_model.cache_clear()


@pytest.fixture
def groq_setup(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "secret-a|secret-b|secret-c")
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "")
    cached_model = models.get_model
    cached_model.cache_clear()
    calls, bindings = [], []
    outcomes = {}

    class FakeGroq:
        def __init__(self, **kwargs):
            self.key = kwargs["api_key"]
            assert "|" not in self.key
            assert kwargs["max_retries"] == (0 if len(models.groq_api_keys()) > 1 else 3)

        def bind(self, **kwargs):
            bindings.append((self.key, kwargs))
            return self

        async def ainvoke(self, messages, **kwargs):
            calls.append((self.key, messages))
            outcome = outcomes.get(self.key)
            if isinstance(outcome, Exception):
                raise outcome
            return AIMessage(content='{"ok":true}')

    monkeypatch.setattr(models, "ChatGroq", FakeGroq)
    yield calls, bindings, outcomes
    cached_model.cache_clear()


@pytest.mark.parametrize("value,expected", [
    (None, ()), ("", ()), (" | | ", ()), ("single", ("single",)),
    (" first |second||first| third ", ("first", "second", "third")),
])
def test_parse_pipe_keys(monkeypatch, value, expected):
    monkeypatch.setattr(config, "GROQ_API_KEY", value)
    assert models.groq_api_keys() == expected


@pytest.mark.parametrize("agent", ["roteador", "rh", "sst", "agenda", "resumo"])
def test_quota_failover_preserves_request_and_keeps_successful_key(groq_setup, caplog, agent):
    calls, bindings, outcomes = groq_setup
    outcomes["secret-a"] = ProviderError(429)
    outcomes["secret-b"] = ProviderError(429)
    backend = models.LanguageModels()
    messages = [HumanMessage(content="Teste")]

    async def scenario():
        assert await backend.complete(agent, messages, json_mode=True) == '{"ok":true}'
        assert await backend.complete(agent, messages, json_mode=True) == '{"ok":true}'

    asyncio.run(scenario())
    assert [key for key, _ in calls] == ["secret-a", "secret-b", "secret-c", "secret-c"]
    assert all(sent == messages for _, sent in calls)
    assert all(options == {"response_format": {"type": "json_object"}} for _, options in bindings)
    assert "secret-" not in caplog.text


def test_all_keys_limited_do_not_loop_or_retry_during_cooldown(groq_setup):
    calls, _, outcomes = groq_setup
    outcomes.update({key: ProviderError(429) for key in models.groq_api_keys()})
    backend = models.LanguageModels()
    for _ in range(2):
        with pytest.raises(ChatError) as error:
            asyncio.run(backend.complete("roteador", []))
        assert error.value.status_code == 503
        assert error.value.reason == "rate_limited"
        assert "secret" not in str(error.value)
    assert [key for key, _ in calls] == ["secret-a", "secret-b", "secret-c"]


@pytest.mark.parametrize("status", [400, 401, 403, 500])
def test_nonquota_errors_do_not_rotate(groq_setup, status):
    calls, _, outcomes = groq_setup
    outcomes["secret-a"] = ProviderError(status)
    with pytest.raises(ChatError) as error:
        asyncio.run(models.LanguageModels().complete("roteador", []))
    assert error.value.status_code == 503
    assert [key for key, _ in calls] == ["secret-a"]


def test_key_becomes_available_after_cooldown(groq_setup, monkeypatch):
    calls, _, outcomes = groq_setup
    clock = [100.0]
    monkeypatch.setattr(models.time, "monotonic", lambda: clock[0])
    outcomes.update({key: ProviderError(429, "30") for key in models.groq_api_keys()})
    backend = models.LanguageModels()
    with pytest.raises(ChatError):
        asyncio.run(backend.complete("roteador", []))
    outcomes.clear()
    clock[0] = 131.0
    assert asyncio.run(backend.complete("roteador", [])) == '{"ok":true}'
    assert len(calls) == 4


@pytest.mark.parametrize("header,expected", [("invalid", 60), (None, 60), ("nan", 60), ("inf", 60), ("-5", 1), ("999999", 86400), ("2", 2)])
def test_retry_after_is_safe(header, expected):
    assert models._groq_cooldown(ProviderError(429, header)) == expected


def test_single_key_remains_supported(groq_setup, monkeypatch):
    calls, _, _ = groq_setup
    monkeypatch.setattr(config, "GROQ_API_KEY", " secret-a ")
    assert asyncio.run(models.LanguageModels().complete("roteador", [])) == '{"ok":true}'
    assert [key for key, _ in calls] == ["secret-a"]


def test_text_content_blocks_are_accepted_but_nontext_is_rejected():
    class TextBlocks:
        async def ainvoke(self, *_args, **_kwargs):
            return AIMessage(content=[{"type": "text", "text": '{"ok":true}'}])

    class ToolBlock:
        async def ainvoke(self, *_args, **_kwargs):
            return AIMessage(content=[{"type": "tool_use", "id": "x", "name": "foo", "input": {}}])

    assert asyncio.run(models.LanguageModels._invoke(TextBlocks(), "juiz", [], False)) == '{"ok":true}'
    with pytest.raises(ChatError):
        asyncio.run(models.LanguageModels._invoke(ToolBlock(), "juiz", [], False))


def test_mistral_fallback_uses_multiple_groq_keys(groq_setup, monkeypatch):
    calls, _, outcomes = groq_setup
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "fake-mistral")
    outcomes["secret-a"] = ProviderError(429)

    class BrokenMistral:
        async def ainvoke(self, *args, **kwargs):
            raise ProviderError(500)

    monkeypatch.setattr(models, "get_model", lambda specialist: BrokenMistral())
    assert asyncio.run(models.LanguageModels().complete("sst", [])) == '{"ok":true}'
    assert [key for key, _ in calls] == ["secret-a", "secret-b"]


def test_real_groq_sdk_switches_authorization_after_http_429(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "fake-a|fake-b")
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "")
    requests = []

    async def send(client, request, **kwargs):
        requests.append(request)
        if request.headers["authorization"] == "Bearer fake-a":
            return httpx.Response(429, request=request, headers={"retry-after": "20"}, json={
                "error": {"message": "Rate limit reached", "type": "rate_limit_error"},
            })
        assert request.headers["authorization"] == "Bearer fake-b"
        return httpx.Response(200, request=request, json={
            "id": "test-completion", "object": "chat.completion", "created": 1,
            "model": models.GROQ_FAST_MODEL,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": '{"ok":true}',
            }}],
        })

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    models.get_model.cache_clear()
    try:
        assert asyncio.run(models.LanguageModels().complete(
            "roteador", [HumanMessage(content="Teste")], json_mode=True,
        )) == '{"ok":true}'
        assert len(requests) == 2
        assert requests[0].content == requests[1].content
    finally:
        models.get_model.cache_clear()
