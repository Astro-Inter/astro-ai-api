import argparse
import json
import os
from datetime import datetime, timedelta, timezone

from langsmith import Client

from app.core import config
from app.observability.report import (
    ScenarioProjection,
    TraceMetric,
    project_scenario,
    report_as_dict,
    summarize_traces,
)


def _positive_number(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("O valor deve ser maior que zero.")
    return number


def _non_negative_number(value: str) -> float:
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError("O valor nao pode ser negativo.")
    return number


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gera indicadores SRE e projecoes a partir dos traces do LangSmith.",
    )
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument(
        "--requests-per-user-week",
        type=_positive_number,
        default=float(os.getenv("SRE_REQUESTS_PER_USER_WEEK", "5")),
        help="Premissa de solicitacoes semanais por usuario (padrao: 5).",
    )
    parser.add_argument(
        "--minutes-saved-per-resolution",
        type=_non_negative_number,
        default=os.getenv("SRE_MINUTES_SAVED_PER_RESOLUTION"),
    )
    parser.add_argument(
        "--hourly-cost-usd",
        type=_non_negative_number,
        default=os.getenv("SRE_HOURLY_COST_USD"),
    )
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    arguments = parser.parse_args()
    if arguments.days <= 0:
        parser.error("--days deve ser maior que zero.")
    if arguments.minutes_saved_per_resolution is None:
        parser.error("Informe --minutes-saved-per-resolution ou configure a variavel equivalente.")
    if arguments.hourly_cost_usd is None:
        parser.error("Informe --hourly-cost-usd ou configure a variavel equivalente.")
    arguments.minutes_saved_per_resolution = float(arguments.minutes_saved_per_resolution)
    arguments.hourly_cost_usd = float(arguments.hourly_cost_usd)
    return arguments


def _feedback_by_run(client: Client, run_ids: list) -> dict[str, int]:
    scores: dict[str, int] = {}
    for offset in range(0, len(run_ids), 100):
        batch = run_ids[offset:offset + 100]
        for feedback in client.list_feedback(
            run_ids=batch,
            feedback_key=["resolved"],
        ):
            if feedback.run_id is not None and feedback.score is not None:
                scores[str(feedback.run_id)] = int(float(feedback.score) >= 0.5)
    return scores


def _load_metrics(client: Client, days: int) -> list[TraceMetric]:
    runs = list(client.list_runs(
        project_name=config.LANGSMITH_PROJECT,
        is_root=True,
        filter='eq(name, "astro_chat")',
        start_time=datetime.now(timezone.utc) - timedelta(days=days),
        select=[
            "id", "start_time", "end_time", "error", "total_cost",
        ],
    ))
    feedback = _feedback_by_run(client, [run.id for run in runs])
    metrics = []
    for run in runs:
        latency_ms = None
        if run.start_time and run.end_time:
            latency_ms = max(0.0, (run.end_time - run.start_time).total_seconds() * 1000)
        metrics.append(TraceMetric(
            run_id=str(run.id),
            latency_ms=latency_ms,
            cost_usd=float(run.total_cost or 0),
            error=bool(run.error),
            resolved=feedback.get(str(run.id)),
        ))
    return metrics


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"US$ {value:.4f}"


def _markdown(summary, scenarios: list[ScenarioProjection], days: int) -> str:
    lines = [
        f"# Relatorio SRE - ultimos {days} dias",
        "",
        "## Metricas observadas",
        "",
        f"- Traces: {summary.trace_count}",
        f"- Indice de erros: {summary.error_rate:.2%} ({summary.error_count})",
        f"- Latencia media: {summary.average_latency_ms:.2f} ms",
        f"- Latencia p95: {summary.p95_latency_ms:.2f} ms",
        f"- Cobertura de resolved: {summary.resolution_coverage:.2%}",
        f"- Taxa de resolucao: {summary.resolution_rate:.2%}",
        f"- Custo total estimado: {_money(summary.total_cost_usd)}",
        f"- Custo por resolucao: {_money(summary.cost_per_resolution_usd)}",
        "",
        "## Projecoes semanais",
        "",
        "| Usuarios | Solicitacoes | Resolucoes | Custo | Beneficio | Retorno liquido | ROI |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scenario in scenarios:
        roi = "n/a" if scenario.roi_percent is None else f"{scenario.roi_percent:.2f}%"
        lines.append(
            f"| {scenario.weekly_users} | {scenario.projected_requests:.0f} | "
            f"{scenario.projected_resolutions:.2f} | {_money(scenario.projected_cost_usd)} | "
            f"{_money(scenario.projected_benefit_usd)} | "
            f"{_money(scenario.projected_net_return_usd)} | {roi} |"
        )
    return "\n".join(lines)


def main() -> None:
    arguments = _arguments()
    if not config.LANGSMITH_API_KEY or not config.LANGSMITH_PROJECT:
        raise SystemExit("Configure LANGSMITH_API_KEY e LANGSMITH_PROJECT.")
    client = Client(
        api_url=config.LANGSMITH_ENDPOINT,
        api_key=config.LANGSMITH_API_KEY,
        workspace_id=config.LANGSMITH_WORKSPACE_ID,
    )
    summary = summarize_traces(_load_metrics(client, arguments.days))
    try:
        scenarios = [
            project_scenario(
                summary,
                weekly_users=users,
                requests_per_user_week=arguments.requests_per_user_week,
                minutes_saved_per_resolution=arguments.minutes_saved_per_resolution,
                hourly_cost_usd=arguments.hourly_cost_usd,
            )
            for users in (100, 1000)
        ]
    except ValueError as error:
        raise SystemExit(str(error)) from None
    report = report_as_dict(summary, scenarios, days=arguments.days)
    print(
        json.dumps(report, ensure_ascii=False, indent=2)
        if arguments.format == "json"
        else _markdown(summary, scenarios, arguments.days)
    )


if __name__ == "__main__":
    main()
