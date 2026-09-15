from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class TraceMetric:
    run_id: str
    latency_ms: float | None
    cost_usd: float
    error: bool
    resolved: int | None


@dataclass(frozen=True)
class SreSummary:
    trace_count: int
    error_count: int
    error_rate: float
    average_latency_ms: float
    p95_latency_ms: float
    total_cost_usd: float
    resolution_evaluated_count: int
    resolution_coverage: float
    resolved_count: int
    resolution_rate: float
    cost_per_resolution_usd: float | None
    average_cost_per_request_usd: float


@dataclass(frozen=True)
class ScenarioProjection:
    weekly_users: int
    requests_per_user_week: float
    projected_requests: float
    projected_resolutions: float
    projected_cost_usd: float
    value_per_resolution_usd: float
    projected_benefit_usd: float
    projected_net_return_usd: float
    roi_percent: float | None


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def summarize_traces(metrics: list[TraceMetric]) -> SreSummary:
    trace_count = len(metrics)
    errors = sum(metric.error for metric in metrics)
    latencies = [
        metric.latency_ms for metric in metrics if metric.latency_ms is not None
    ]
    evaluated = [metric for metric in metrics if metric.resolved is not None]
    resolved_count = sum(metric.resolved or 0 for metric in evaluated)
    evaluated_cost = sum(metric.cost_usd for metric in evaluated)
    total_cost = sum(metric.cost_usd for metric in metrics)

    return SreSummary(
        trace_count=trace_count,
        error_count=errors,
        error_rate=errors / trace_count if trace_count else 0.0,
        average_latency_ms=sum(latencies) / len(latencies) if latencies else 0.0,
        p95_latency_ms=_percentile(latencies, 0.95),
        total_cost_usd=total_cost,
        resolution_evaluated_count=len(evaluated),
        resolution_coverage=len(evaluated) / trace_count if trace_count else 0.0,
        resolved_count=resolved_count,
        resolution_rate=resolved_count / len(evaluated) if evaluated else 0.0,
        cost_per_resolution_usd=(
            evaluated_cost / resolved_count if resolved_count else None
        ),
        average_cost_per_request_usd=total_cost / trace_count if trace_count else 0.0,
    )


def project_scenario(
    summary: SreSummary,
    *,
    weekly_users: int,
    requests_per_user_week: float,
    minutes_saved_per_resolution: float,
    hourly_cost_usd: float,
) -> ScenarioProjection:
    if weekly_users <= 0:
        raise ValueError("weekly_users deve ser positivo.")
    if requests_per_user_week <= 0:
        raise ValueError("requests_per_user_week deve ser positivo.")
    if minutes_saved_per_resolution < 0 or hourly_cost_usd < 0:
        raise ValueError("Premissas de beneficio nao podem ser negativas.")
    if not summary.resolution_evaluated_count:
        raise ValueError("Nao ha traces avaliados com o feedback resolved.")

    requests = weekly_users * requests_per_user_week
    resolutions = requests * summary.resolution_rate
    projected_cost = requests * summary.average_cost_per_request_usd
    value_per_resolution = minutes_saved_per_resolution / 60 * hourly_cost_usd
    benefit = resolutions * value_per_resolution
    net_return = benefit - projected_cost
    roi = (
        net_return / projected_cost * 100
        if projected_cost > 0
        else None
    )
    return ScenarioProjection(
        weekly_users=weekly_users,
        requests_per_user_week=requests_per_user_week,
        projected_requests=requests,
        projected_resolutions=resolutions,
        projected_cost_usd=projected_cost,
        value_per_resolution_usd=value_per_resolution,
        projected_benefit_usd=benefit,
        projected_net_return_usd=net_return,
        roi_percent=roi,
    )


def report_as_dict(
    summary: SreSummary, scenarios: list[ScenarioProjection], *, days: int,
) -> dict:
    return {
        "period_days": days,
        "observed": asdict(summary),
        "scenarios": [asdict(scenario) for scenario in scenarios],
    }
