import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from app.core import config
from app.infrastructure.database.access import AccessLookupError, PostgresAccessRoles


class FakeCursor:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query, parameters):
        self.calls.append((query, parameters))

    def fetchone(self):
        return self.value


class FakeConnection:
    def __init__(self, value):
        self.db_cursor = FakeCursor(value)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def cursor(self):
        return self.db_cursor


@pytest.mark.parametrize("database_value,expected", [
    ("ADMIN", "ADMIN"),
    ("GESTOR", "GESTOR"),
    ("GESTOR_WORKSPACE", "GESTOR_WORKSPACE"),
    ("COLABORADOR", "COLABORADOR"),
    ("SEM_ACESSO", None),
])
def test_access_role_uses_parameterized_database_function(monkeypatch, database_value, expected):
    async def scenario():
        calls = []
        connection = FakeConnection((database_value,))

        def connect(*args, **kwargs):
            calls.append((args, kwargs))
            return connection

        monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
        result = await PostgresAccessRoles(connect).get_role("firebase-uid")
        assert result == expected
        assert calls == [(('postgresql://test:test@localhost/astro',), {
            "autocommit": True, "connect_timeout": 5,
            "application_name": "astro-ai-api",
            "options": "-c statement_timeout=5000 -c default_transaction_read_only=on",
        })]
        assert connection.db_cursor.calls == [(
            "SELECT fn_retornar_nivel_acesso(%s)", ("firebase-uid",),
        )]

    asyncio.run(scenario())


@pytest.mark.parametrize("message,reason", [
    ("remaining connection slots are reserved private-host secret firebase-uid", "limite_conexoes"),
    ("too many connections private-host secret firebase-uid", "limite_conexoes"),
    ("password authentication failed private-host secret firebase-uid", "autenticacao_banco"),
])
def test_connection_configuration_failures_are_not_retried_or_exposed(monkeypatch, caplog, message, reason):
    calls = []

    def connect(*args, **kwargs):
        calls.append(True)
        raise psycopg.OperationalError(message)

    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
    with pytest.raises(AccessLookupError):
        asyncio.run(PostgresAccessRoles(connect).get_role("firebase-uid"))
    assert len(calls) == 1
    assert f"motivo={reason}" in caplog.text
    for secret in ("private-host", "secret", "firebase-uid"):
        assert secret not in caplog.text


def test_authorization_bounds_concurrent_connections_and_releases_slots(monkeypatch):
    monkeypatch.setattr(config, "POSTGRES_AUTH_MAX_CONCURRENCY", 2)
    lock = threading.Lock()
    active = 0
    peak = 0

    class Connection(FakeConnection):
        def __enter__(self):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03)
            return self

        def __exit__(self, *args):
            nonlocal active
            with lock:
                active -= 1

    roles = PostgresAccessRoles(lambda *args, **kwargs: Connection(("GESTOR",)))
    with ThreadPoolExecutor(max_workers=8) as executor:
        rows = list(executor.map(roles._query_role, ["fake-uid"] * 8))
    assert rows == [("GESTOR",)] * 8
    assert peak == 2 and active == 0


def test_failed_connection_does_not_leak_authorization_slot(monkeypatch):
    monkeypatch.setattr(config, "POSTGRES_AUTH_MAX_CONCURRENCY", 1)
    calls = []

    def connect(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise psycopg.OperationalError("too many connections")
        return FakeConnection(("GESTOR",))

    roles = PostgresAccessRoles(connect)
    with pytest.raises(psycopg.OperationalError):
        roles._query_role("fake-uid")
    assert roles._query_role("fake-uid") == ("GESTOR",)


@pytest.mark.parametrize("row", [None, (None,), ("DESCONHECIDO",)])
def test_invalid_database_role_fails_closed(monkeypatch, row):
    async def scenario():
        def connect(*args, **kwargs):
            return FakeConnection(row)

        monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
        with pytest.raises(AccessLookupError):
            await PostgresAccessRoles(connect).get_role("firebase-uid")

    asyncio.run(scenario())


def test_database_errors_hide_connection_details(monkeypatch):
    async def scenario():
        def connect(*args, **kwargs):
            raise psycopg.OperationalError("postgresql://user:secret@private-host/astro")

        monkeypatch.setattr(config, "DATABASE_URL", "postgresql://user:secret@private-host/astro")
        with pytest.raises(AccessLookupError) as error:
            await PostgresAccessRoles(connect).get_role("firebase-uid")
        assert "secret" not in str(error.value)

    asyncio.run(scenario())


def test_authorization_retries_transient_connection_once(monkeypatch, caplog):
    async def scenario():
        calls = []

        def connect(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise psycopg.OperationalError("private-host secret firebase-uid")
            return FakeConnection(("GESTOR",))

        monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
        assert await PostgresAccessRoles(connect).get_role("firebase-uid") == "GESTOR"
        assert len(calls) == 2
        assert all("default_transaction_read_only=on" in call["options"] for call in calls)
        assert "secret" not in caplog.text
        assert "firebase-uid" not in caplog.text
        assert "private-host" not in caplog.text

    asyncio.run(scenario())


@pytest.mark.parametrize("error,attempts", [
    (psycopg.OperationalError("secret"), 2),
    (psycopg.errors.InvalidPassword("secret"), 1),
    (psycopg.errors.UndefinedFunction("secret"), 1),
    (psycopg.errors.QueryCanceled("secret"), 1),
])
def test_authorization_retry_is_bounded_and_never_grants_access(monkeypatch, caplog, error, attempts):
    async def scenario():
        calls = []

        def connect(*args, **kwargs):
            calls.append(kwargs)
            raise error

        monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
        with pytest.raises(AccessLookupError):
            await PostgresAccessRoles(connect).get_role("firebase-uid")
        assert len(calls) == attempts
        assert "secret" not in caplog.text

    asyncio.run(scenario())
