"""Orçamento compartilhado de conexões PostgreSQL por processo, sem pool ocioso."""

import threading
from contextlib import contextmanager

import psycopg

from app.core import config


_slots = threading.BoundedSemaphore(config.POSTGRES_MAX_CONCURRENCY)


@contextmanager
def read_only_connection(dsn, *, connect=None):
    # Todos os módulos compartilham as vagas. A conexão só é aberta após obter
    # uma vaga e é fechada antes de devolvê-la, inclusive em caso de exceção.
    if not _slots.acquire(timeout=5):
        raise psycopg.OperationalError("Limite local de conexoes PostgreSQL atingido")
    try:
        factory = connect or psycopg.connect
        with factory(
            dsn,
            autocommit=True,
            connect_timeout=5,
            application_name="astro-ai-api",
            options="-c statement_timeout=5000 -c default_transaction_read_only=on",
        ) as connection:
            yield connection
    finally:
        _slots.release()
