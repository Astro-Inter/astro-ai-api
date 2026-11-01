import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.core import config
from app.infrastructure import mcp_fetch, public_source_cache as cache

URL = "https://www.gov.br/pagina-oficial"


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(config, "REDIS_URL", "redis://fake")
    monkeypatch.setattr(config, "FETCH_MCP_REDIS_CACHE_ENABLED", True)
    monkeypatch.setattr(config, "FETCH_MCP_CACHE_TTL_SECONDS", 900)
    monkeypatch.setattr(config, "FETCH_MCP_ALLOWED_DOMAINS", "gov.br")
    clock = [1000.0]
    monkeypatch.setattr(cache, "_now", lambda: clock[0])
    monkeypatch.setattr(cache, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(mcp_fetch, "monotonic", lambda: clock[0])
    calls = []
    stored = {}
    commands = []

    class Redis:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, key):
            commands.append(("get", key))
            return stored.get(key)

        async def set(self, key, value, **kwargs):
            commands.append(("set", key, kwargs))
            stored[key] = value.encode()

    monkeypatch.setattr("redis.asyncio.Redis.from_url", lambda *a, **kw: Redis())

    @asynccontextmanager
    async def stdio(*args, **kwargs):
        yield ("reader", "writer")

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def initialize(self):
            pass

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name="fetch")])

        async def call_tool(self, name, arguments):
            calls.append(arguments)
            return SimpleNamespace(
                isError=False,
                content=[SimpleNamespace(text="Conteúdo público de teste.")],
                structuredContent=None,
            )

    monkeypatch.setattr(mcp_fetch, "stdio_client", stdio)
    monkeypatch.setattr(mcp_fetch, "ClientSession", lambda *a: Session())
    return SimpleNamespace(
        clock=clock, calls=calls, stored=stored, commands=commands, redis=Redis
    )


def test_cross_instance_hit_skips_mcp_and_keeps_wire_contract(runtime):
    async def scenario():
        first = await mcp_fetch.FetchMCPClient().fetch(URL)
        second = await mcp_fetch.FetchMCPClient().fetch(URL)
        assert first == second and first["protocolo"] == "MCP"
        assert (
            len(runtime.calls) == 1
            and len([c for c in runtime.commands if c[0] == "set"]) == 1
        )
        second["conteudo"] = "mutado"
        assert (await mcp_fetch.FetchMCPClient().fetch(URL))["conteudo"] == first[
            "conteudo"
        ]
        key = next(iter(runtime.stored))
        assert key.startswith("ia:cache:fonte_publica:v1:") and URL not in key

    asyncio.run(scenario())


def test_cache_hit_never_renews_redis_or_memory_expiration(runtime):
    async def scenario():
        await mcp_fetch.FetchMCPClient().fetch(URL)
        runtime.clock[0] += 890
        other = mcp_fetch.FetchMCPClient()
        await other.fetch(URL)
        assert len(runtime.calls) == 1
        runtime.clock[0] += 11
        await other.fetch(URL)
        assert len(runtime.calls) == 2
        assert len([c for c in runtime.commands if c[0] == "set"]) == 2

    asyncio.run(scenario())


def test_ttl_reduction_cannot_resurrect_older_source(runtime, monkeypatch):
    async def scenario():
        await mcp_fetch.FetchMCPClient().fetch(URL)
        runtime.clock[0] += 100
        monkeypatch.setattr(config, "FETCH_MCP_CACHE_TTL_SECONDS", 30)
        await mcp_fetch.FetchMCPClient().fetch(URL)
        assert len(runtime.calls) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change", ["version", "url", "expired", "error", "oversized", "malformed"]
)
def test_invalid_shared_data_falls_back_to_original_source(runtime, change):
    async def scenario():
        await mcp_fetch.FetchMCPClient().fetch(URL)
        key = next(iter(runtime.stored))
        value = json.loads(runtime.stored[key])
        if change == "version":
            value["versao"] = 2
        if change == "url":
            value["resultado"]["url"] = "https://evil.example"
        if change == "expired":
            value["expira_em"] = 999
        if change == "error":
            value["resultado"]["status"] = "indisponivel"
        runtime.stored[key] = (
            ("x" * (cache.MAX_BYTES + 1)).encode()
            if change == "oversized"
            else b"{"
            if change == "malformed"
            else json.dumps(value).encode()
        )
        result = await mcp_fetch.FetchMCPClient().fetch(URL)
        assert result["status"] == "ok" and len(runtime.calls) == 2

    asyncio.run(scenario())


def test_allowlist_is_rechecked_even_for_existing_hit(runtime, monkeypatch):
    async def scenario():
        client = mcp_fetch.FetchMCPClient()
        await client.fetch(URL)
        monkeypatch.setattr(config, "FETCH_MCP_ALLOWED_DOMAINS", "example.org")
        assert (await client.fetch(URL))["status"] == "nao_autorizado"
        assert (await mcp_fetch.FetchMCPClient().fetch(URL))[
            "status"
        ] == "nao_autorizado"
        assert len(runtime.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("disabled", ["ttl", "toggle", "url"])
def test_opt_out_and_query_urls_do_not_use_shared_redis(runtime, monkeypatch, disabled):
    async def scenario():
        url = URL
        if disabled == "ttl":
            monkeypatch.setattr(config, "FETCH_MCP_CACHE_TTL_SECONDS", 0)
        if disabled == "toggle":
            monkeypatch.setattr(config, "FETCH_MCP_REDIS_CACHE_ENABLED", False)
        if disabled == "url":
            url += "?email=not-for-shared-cache"
        await mcp_fetch.FetchMCPClient().fetch(url)
        assert not runtime.commands

    asyncio.run(scenario())


def test_redis_failure_is_optional_and_has_bounded_cooldown(
    runtime, monkeypatch, caplog
):
    count = []

    def failed(*args, **kwargs):
        count.append(1)
        raise RuntimeError("redis-password-private")

    monkeypatch.setattr("redis.asyncio.Redis.from_url", failed)

    async def scenario():
        client = mcp_fetch.FetchMCPClient()
        assert (await client.fetch(URL))["status"] == "ok"
        assert (await client.fetch(URL + "/outra"))["status"] == "ok"
        assert len(count) == 1 and len(runtime.calls) == 2

    asyncio.run(scenario())
    assert "redis-password-private" not in caplog.text


def test_request_variants_have_separate_keys(runtime):
    async def scenario():
        for options in [{}, {"max_length": 100}, {"start_index": 10}, {"raw": True}]:
            await mcp_fetch.FetchMCPClient().fetch(URL, **options)
        assert len(runtime.calls) == 4 and len(runtime.stored) == 4

    asyncio.run(scenario())


def test_process_cache_is_bounded_and_ttl_zero_disables_both_levels(
    runtime, monkeypatch
):
    client = mcp_fetch.FetchMCPClient()
    for n in range(150):
        client._remember((n,), {"conteudo": "teste"}, 900)
    assert len(client._cache) == 128

    async def scenario():
        await client.fetch(URL)
        monkeypatch.setattr(config, "FETCH_MCP_CACHE_TTL_SECONDS", 0)
        await client.fetch(URL)
        assert len(runtime.calls) == 2

    asyncio.run(scenario())


def test_upstream_failure_is_not_cached(runtime, monkeypatch):
    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def initialize(self):
            pass

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name="fetch")])

        async def call_tool(self, *args, **kwargs):
            return SimpleNamespace(isError=True)

    monkeypatch.setattr(mcp_fetch, "ClientSession", lambda *a: Session())

    async def scenario():
        assert (await mcp_fetch.FetchMCPClient().fetch(URL))["status"] == "indisponivel"
        assert not runtime.stored and not any(c[0] == "set" for c in runtime.commands)

    asyncio.run(scenario())


def test_slow_redis_does_not_consume_the_external_fetch_timeout(runtime, monkeypatch):
    async def slow(*args):
        await asyncio.sleep(5)

    monkeypatch.setattr(runtime.redis, "get", slow)

    async def scenario():
        result = await asyncio.wait_for(mcp_fetch.FetchMCPClient().fetch(URL), 1)
        assert result["status"] == "ok" and len(runtime.calls) == 1

    asyncio.run(scenario())
