import pytest

from app.observability.chat import ChatObservation, classify_resolution
from app.observability.report import TraceMetric, project_scenario, summarize_traces


@pytest.mark.parametrize(("result", "expected"), [
    ({"resposta": "Resposta", "guardar_turno": True, "rota": "direta"}, (1, "resolvido")),
    ({
        "resposta": "Confirma?", "guardar_turno": True, "rota": "agenda",
        "acao_pendente": {"tipo": "criar_evento_google_calendar"},
    }, (0, "aguardando_confirmacao")),
    ({
        "resposta": "Indisponivel", "guardar_turno": True, "rota": "sst",
        "resultado": {"status": "indisponivel"},
    }, (0, "indisponivel")),
    ({
        "resposta": "Nenhuma notificacao", "guardar_turno": True,
        "rota": "notificacoes", "resultado": {"status": "sem_dados"},
    }, (1, "resolvido")),
    ({
        "resposta": "Nao posso ajudar", "guardar_turno": False, "rota": "fim",
        "agentes_chamados": ["guardrail_entrada"],
    }, (0, "resposta_bloqueada")),
])
def test_resolution_is_deterministic(result, expected):
    assert classify_resolution(result) == expected


def test_observation_exposes_aggregate_agent_metrics(monkeypatch):
    values = iter([10.0, 10.2, 10.5, 10.7, 11.0])
    monkeypatch.setattr("app.observability.chat.time.perf_counter", lambda: next(values))
    observation = ChatObservation()
    first = observation.start_agent("roteador")
    observation.finish_agent(first)
    second = observation.start_agent("sst")
    observation.finish_agent(second)
    metadata = observation.metadata()

    assert metadata["agent_call_count"] == 2
    assert metadata["agent_transition_count"] == 1
    assert metadata["agent_latency_total_ms"] == pytest.approx(400)
    assert metadata["agent_transition_total_ms"] == pytest.approx(300)
    assert '"roteador":200.0' in metadata["agent_latencies_ms"]
    assert '"roteador->sst":300.0' in metadata["agent_transitions_ms"]
    assert observation.feedback_scores() == {
        "resolved": 0,
        "agent_transition_avg_ms": pytest.approx(300),
    }


def test_sre_summary_and_projections_use_observed_metrics():
    summary = summarize_traces([
        TraceMetric("a", latency_ms=100, cost_usd=0.20, error=False, resolved=1),
        TraceMetric("b", latency_ms=300, cost_usd=0.10, error=True, resolved=0),
    ])

    assert summary.error_rate == pytest.approx(0.5)
    assert summary.average_latency_ms == pytest.approx(200)
    assert summary.p95_latency_ms == pytest.approx(290)
    assert summary.resolution_rate == pytest.approx(0.5)
    assert summary.cost_per_resolution_usd == pytest.approx(0.30)
    assert summary.average_cost_per_request_usd == pytest.approx(0.15)

    scenario = project_scenario(
        summary,
        weekly_users=100,
        requests_per_user_week=5,
        minutes_saved_per_resolution=12,
        hourly_cost_usd=30,
    )
    assert scenario.projected_requests == 500
    assert scenario.projected_resolutions == pytest.approx(250)
    assert scenario.projected_cost_usd == pytest.approx(75)
    assert scenario.projected_benefit_usd == pytest.approx(1500)
    assert scenario.roi_percent == pytest.approx(1900)


def test_projection_requires_resolution_feedback():
    summary = summarize_traces([
        TraceMetric("a", latency_ms=100, cost_usd=0.10, error=False, resolved=None),
    ])
    with pytest.raises(ValueError, match="resolved"):
        project_scenario(
            summary,
            weekly_users=100,
            requests_per_user_week=5,
            minutes_saved_per_resolution=10,
            hourly_cost_usd=20,
        )
