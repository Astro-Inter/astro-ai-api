import json
import logging
import time
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterator

from langsmith import Client, trace
from langsmith import utils as langsmith_utils
from langsmith.run_helpers import get_tracing_context

from app.core import config


logger = logging.getLogger(__name__)

UNRESOLVED_STATUSES = {
    "aguardando_confirmacao",
    "ambiguo",
    "bloqueado",
    "erro",
    "esclarecer",
    "indisponivel",
    "nao_autorizado",
    "nao_conectado",
}


def classify_resolution(result: dict[str, Any]) -> tuple[int, str]:
    """Classifica uma resposta concluida sem pedir ao modelo que se autoavalie."""
    if not result.get("resposta"):
        return 0, "sem_resposta"
    if result.get("acao_pendente"):
        return 0, "aguardando_confirmacao"
    if not result.get("guardar_turno"):
        return 0, "resposta_bloqueada"
    if result.get("rota") == "fim" and result.get("agentes_chamados") == [
        "guardrail_entrada"
    ]:
        return 0, "interrompido_no_guardrail_entrada"

    status = result.get("resultado", {}).get("status")
    if status in UNRESOLVED_STATUSES:
        return 0, str(status)
    return 1, "resolvido"


@dataclass
class AgentMeasurement:
    name: str
    started_at: float


@dataclass
class ChatObservation:
    started_at: float = field(default_factory=time.perf_counter)
    resolved: int = 0
    resolution_status: str = "em_processamento"
    route: str = ""
    response_length: int = 0
    error_type: str = ""
    error_status_code: int | None = None
    agent_calls: list[tuple[str, float]] = field(default_factory=list)
    transitions: list[tuple[str, str, float]] = field(default_factory=list)
    last_agent_name: str | None = None
    last_agent_finished_at: float | None = None

    def start_agent(self, name: str) -> AgentMeasurement:
        now = time.perf_counter()
        if self.last_agent_name and self.last_agent_finished_at is not None:
            self.transitions.append(
                (self.last_agent_name, name, max(0.0, now - self.last_agent_finished_at))
            )
        return AgentMeasurement(name=name, started_at=now)

    def finish_agent(self, measurement: AgentMeasurement) -> None:
        now = time.perf_counter()
        self.agent_calls.append(
            (measurement.name, max(0.0, now - measurement.started_at))
        )
        self.last_agent_name = measurement.name
        self.last_agent_finished_at = now

    def mark_result(self, result: dict[str, Any]) -> None:
        self.resolved, self.resolution_status = classify_resolution(result)
        self.route = str(result.get("rota") or "direta")
        self.response_length = len(str(result.get("resposta") or ""))

    def mark_error(self, error: BaseException) -> None:
        self.resolved = 0
        self.resolution_status = "erro"
        self.error_type = type(error).__name__
        status_code = getattr(error, "status_code", None)
        self.error_status_code = status_code if isinstance(status_code, int) else None

    def metadata(self) -> dict[str, Any]:
        agent_totals: defaultdict[str, float] = defaultdict(float)
        for name, duration in self.agent_calls:
            agent_totals[name] += duration * 1000
        transition_totals: defaultdict[str, float] = defaultdict(float)
        for source, target, duration in self.transitions:
            transition_totals[f"{source}->{target}"] += duration * 1000

        agent_durations = [duration * 1000 for _, duration in self.agent_calls]
        transition_durations = [duration * 1000 for _, _, duration in self.transitions]
        metadata: dict[str, Any] = {
            "resolved": self.resolved,
            "resolution_status": self.resolution_status,
            "route": self.route or "erro",
            "total_response_ms": round((time.perf_counter() - self.started_at) * 1000, 2),
            "agent_call_count": len(self.agent_calls),
            "agent_latency_total_ms": round(sum(agent_durations), 2),
            "agent_latency_avg_ms": round(
                sum(agent_durations) / len(agent_durations), 2
            ) if agent_durations else 0.0,
            "agent_latency_max_ms": round(max(agent_durations), 2) if agent_durations else 0.0,
            "agent_transition_count": len(self.transitions),
            "agent_transition_total_ms": round(sum(transition_durations), 2),
            "agent_transition_avg_ms": round(
                sum(transition_durations) / len(transition_durations), 2
            ) if transition_durations else 0.0,
            "agent_transition_max_ms": round(max(transition_durations), 2)
            if transition_durations else 0.0,
            "agent_latencies_ms": json.dumps(
                {name: round(value, 2) for name, value in agent_totals.items()},
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            "agent_transitions_ms": json.dumps(
                {name: round(value, 2) for name, value in transition_totals.items()},
                ensure_ascii=True,
                separators=(",", ":"),
            ),
        }
        if self.error_type:
            metadata["error_type"] = self.error_type
        if self.error_status_code is not None:
            metadata["error_status_code"] = self.error_status_code
        return metadata


_current_observation: ContextVar[ChatObservation | None] = ContextVar(
    "astro_chat_observation", default=None,
)


def start_agent_measurement(name: str) -> AgentMeasurement | None:
    observation = _current_observation.get()
    return observation.start_agent(name) if observation else None


def finish_agent_measurement(measurement: AgentMeasurement | None) -> None:
    observation = _current_observation.get()
    if observation and measurement:
        observation.finish_agent(measurement)


@lru_cache(maxsize=1)
def _langsmith_client() -> Client:
    return Client(
        api_url=config.LANGSMITH_ENDPOINT,
        api_key=config.LANGSMITH_API_KEY,
        workspace_id=config.LANGSMITH_WORKSPACE_ID,
    )


def _tracing_enabled() -> bool:
    return langsmith_utils.tracing_is_enabled(get_tracing_context()) is True


def _record_resolution_feedback(trace_id, score: int) -> None:
    if not trace_id or not _tracing_enabled():
        return
    try:
        _langsmith_client().create_feedback(
            run_id=trace_id,
            trace_id=trace_id,
            key="resolved",
            score=score,
            source_info={"source": "astro_backend", "method": "deterministic"},
        )
    except Exception as error:
        logger.warning(
            "Falha ao registrar feedback de resolucao no LangSmith: %s",
            type(error).__name__,
        )


@contextmanager
def observe_chat(*, reused_session: bool, markdown: bool) -> Iterator[ChatObservation]:
    """Cria o trace raiz da requisicao e nunca inclui mensagem ou UID nos metadados."""
    observation = ChatObservation()
    token = _current_observation.set(observation)
    trace_id = None
    try:
        with trace(
            "astro_chat",
            run_type="chain",
            inputs={
                "session_reused": reused_session,
                "response_format": "markdown" if markdown else "plain_text",
            },
            project_name=config.LANGSMITH_PROJECT,
            tags=["chat", "multiagente", config.APP_ENV],
            metadata={
                "environment": config.APP_ENV,
                "session_reused": reused_session,
                "response_format": "markdown" if markdown else "plain_text",
            },
            client=_langsmith_client() if config.LANGSMITH_API_KEY else None,
        ) as run:
            trace_id = run.id
            try:
                yield observation
            except BaseException as error:
                observation.mark_error(error)
                run.metadata.update(observation.metadata())
                run.tags.extend(["resolved:0", "result:error"])
                raise
            else:
                if observation.resolution_status == "em_processamento":
                    observation.resolution_status = "sem_classificacao"
                run.metadata.update(observation.metadata())
                run.tags.extend([
                    f"resolved:{observation.resolved}",
                    f"result:{observation.resolution_status}",
                ])
                run.end(outputs={
                    "resolved": observation.resolved,
                    "resolution_status": observation.resolution_status,
                    "route": observation.route,
                    "response_length": observation.response_length,
                })
    finally:
        _current_observation.reset(token)
        _record_resolution_feedback(trace_id, observation.resolved)
