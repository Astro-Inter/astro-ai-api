"""Garantias compartilhadas para testes que instanciam agentes LangChain."""

import pytest
from langsmith import tracing_context


@pytest.fixture(autouse=True)
def sem_tracing_remoto(monkeypatch):
    from app.core import config
    # Unit tests never opt into remote moderation through a developer's .env.
    monkeypatch.setattr(config, "LLAMA_GUARD_MODE", "off")
    # Nenhum prompt ou dado de fixture deve sair para o LangSmith durante testes.
    # Also avoid exporting local test logs to the user's production OTLP sink.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "")
    with tracing_context(enabled=False):
        yield
