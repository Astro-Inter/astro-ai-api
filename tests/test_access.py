import asyncio

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
    ("FUNCIONARIO", "FUNCIONARIO"),
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
            "options": "-c statement_timeout=5000 -c default_transaction_read_only=on",
        })]
        assert connection.db_cursor.calls == [(
            "SELECT fn_retornar_nivel_acesso(%s)", ("firebase-uid",),
        )]

    asyncio.run(scenario())


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
