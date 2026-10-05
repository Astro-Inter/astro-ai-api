import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

import psycopg
import pytest

from app.infrastructure.database import connections
from app.infrastructure.database.access import PostgresAccessRoles
from app.modules.rh import tools as rh


class FakeConnection:
    def __init__(self):
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def cursor(self):
        return nullcontext(self)

    def execute(self, *args):
        pass

    def fetchone(self):
        return ("GESTOR",)


@pytest.mark.parametrize("failure", ["connect", "query", None])
def test_shared_connection_releases_budget_after_success_or_failure(monkeypatch, failure):
    slot = threading.BoundedSemaphore(1)
    monkeypatch.setattr(connections, "_slots", slot)
    connection = FakeConnection()
    calls = []

    def connect(dsn, **kwargs):
        calls.append(kwargs)
        if failure == "connect":
            raise psycopg.OperationalError("fake failure")
        return connection

    def run():
        with connections.read_only_connection("fake-dsn", connect=connect):
            if failure == "query":
                raise RuntimeError("fake query failure")

    if failure:
        with pytest.raises((psycopg.OperationalError, RuntimeError)):
            run()
    else:
        run()
    assert slot.acquire(blocking=False)
    slot.release()
    assert failure == "connect" or connection.closed
    assert calls[0]["application_name"] == "astro-ai-api"
    assert "default_transaction_read_only=on" in calls[0]["options"]


def test_local_exhaustion_never_opens_an_extra_connection(monkeypatch):
    class UnavailableSlots:
        def acquire(self, *, timeout):
            assert timeout == 5
            return False

        def release(self):
            pytest.fail("No slot was acquired")

    monkeypatch.setattr(connections, "_slots", UnavailableSlots())
    with pytest.raises(psycopg.OperationalError, match="Limite local"):
        with connections.read_only_connection(
            "fake-dsn", connect=lambda *a, **kw: pytest.fail("Must not connect"),
        ):
            pytest.fail("Must not execute a query")


def test_authorization_and_tools_share_the_same_budget(monkeypatch):
    monkeypatch.setattr(connections, "_slots", threading.BoundedSemaphore(2))
    active = peak = 0
    lock = threading.Lock()

    class CountedConnection(FakeConnection):
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
            super().__exit__(*args)

    def connect(*args, **kwargs):
        return CountedConnection()

    monkeypatch.setattr(connections.psycopg, "connect", connect)
    access = PostgresAccessRoles(connect)

    def lookup(index):
        if index % 2:
            return access._query_role("fake-user")
        with rh.get_conn() as conn:
            return conn.fetchone()

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(lookup, range(8))) == [("GESTOR",)] * 8
    assert peak == 2 and active == 0
