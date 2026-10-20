"""Telemetria sem payloads de conversa, preservando métricas operacionais."""

from contextlib import contextmanager
from functools import lru_cache

from langchain_core.tracers.context import tracing_v2_enabled
from langsmith import Client, utils

from app.core import config
from app.core.pii import redact_trace_data


def private_trace_outputs(outputs):
    # Mantém os sinais usados pelo LangSmith para métricas/custo. Textos,
    # mensagens, ferramentas e estados completos nunca saem nos outputs.
    safe = {}
    for key in ("resolved", "resolution_status", "route", "response_length"):
        if key in outputs:
            safe[key] = outputs[key]
    for key in ("usage_metadata", "token_usage"):
        if isinstance(outputs.get(key), dict):
            safe[key] = {name: count for name, count in outputs[key].items() if type(count) in {int, float}}
    llm = outputs.get("llm_output")
    if isinstance(llm, dict):
        safe["llm_output"] = private_trace_outputs(llm)
        if isinstance(llm.get("model_name"), str):
            safe["llm_output"]["model_name"] = llm["model_name"]
    return redact_trace_data(safe)


@lru_cache(maxsize=1)
def private_langsmith_client():
    return Client(
        api_url=config.LANGSMITH_ENDPOINT,
        api_key=config.LANGSMITH_API_KEY,
        workspace_id=config.LANGSMITH_WORKSPACE_ID,
        hide_inputs=True,
        hide_outputs=private_trace_outputs,
        anonymizer=redact_trace_data,
    )


@contextmanager
def private_tracing():
    # Respeita tracing_context(enabled=False), inclusive nos testes. O cliente
    # é passado também ao callback LangChain, que usa outro default client.
    if config.LANGSMITH_API_KEY and utils.tracing_is_enabled():
        with tracing_v2_enabled(client=private_langsmith_client(), project_name=config.LANGSMITH_PROJECT):
            yield
    else:
        yield
