"""Garantias compartilhadas para testes que instanciam agentes LangChain."""

import pytest
from langsmith import tracing_context


@pytest.fixture(autouse=True)
def sem_tracing_remoto():
    # Nenhum prompt ou dado de fixture deve sair para o LangSmith durante testes.
    with tracing_context(enabled=False):
        yield
