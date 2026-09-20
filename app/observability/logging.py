"""Logging estruturado com exportacao OTLP opcional para o Grafana Cloud."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import traceback
from copy import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from typing import TextIO

from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource


_SENSITIVE_VALUE = re.compile(
    r"(?i)(authorization|api[-_ ]?key|password|secret|token|credentials?)"
    r"(\s*[:=]\s*)(?:basic\s+|bearer\s+)?[^\s,;]+"
)
_URL_CREDENTIALS = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:/\s]+:)[^@/\s]+(@)")
_SENSITIVE_ATTRIBUTE_NAMES = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "access_token",
    "refresh_token",
}


def _service_version() -> str:
    try:
        return version("astro-ai-api")
    except PackageNotFoundError:
        return "0.1.0"


def _redact(value: str) -> str:
    value = _SENSITIVE_VALUE.sub(r"\1\2[REDACTED]", value)
    return _URL_CREDENTIALS.sub(r"\1[REDACTED]\2", value)


class StructuredJsonFormatter(logging.Formatter):
    """Formato de console estavel e seguro para leitura humana ou por agentes."""

    def __init__(
        self,
        *,
        service_name: str,
        environment: str,
        service_version: str,
        worker_name: str | None = None,
    ) -> None:
        super().__init__()
        self._base = {
            "service.name": service_name,
            "service.version": service_version,
            "environment": environment,
        }
        if worker_name:
            self._base["worker"] = worker_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc,
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            **self._base,
            "logger": record.name,
            "message": _redact(record.getMessage()),
        }
        for attribute in ("job", "operation", "duration", "status", "error"):
            value = getattr(record, attribute, None)
            if value is not None:
                payload[attribute] = _redact(value) if isinstance(value, str) else (
                    value if isinstance(value, (int, float, bool)) else str(value)
                )
        if record.exc_info:
            payload["error"] = _redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class _SkipExporterInternals(logging.Filter):
    """Evita que falhas do proprio exportador sejam reenviadas recursivamente."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith(("opentelemetry", "urllib3"))


class _SafeOtelLoggingHandler(LoggingHandler):
    """Remove credenciais obvias do corpo e do stack trace antes da exportacao."""

    def _translate(self, record: logging.LogRecord):
        safe_record = copy(record)
        safe_record.msg = _redact(record.getMessage())
        safe_record.args = ()
        for name, value in vars(safe_record).items():
            normalized_name = name.lower().replace("-", "_")
            if normalized_name in _SENSITIVE_ATTRIBUTE_NAMES:
                setattr(safe_record, name, "[REDACTED]")
            elif isinstance(value, str):
                setattr(safe_record, name, _redact(value))
        if record.exc_info:
            exception_type, _, _ = record.exc_info
            setattr(safe_record, "exception.type", exception_type.__name__)
            setattr(
                safe_record,
                "exception.stacktrace",
                _redact("".join(traceback.format_exception(*record.exc_info))),
            )
            safe_record.exc_info = None
            safe_record.exc_text = None
        return super()._translate(safe_record)


@dataclass
class ObservabilityRuntime:
    """Recursos instalados no logging raiz e seu encerramento ordenado."""

    service_name: str
    otlp_enabled: bool
    console_handler: logging.Handler
    previous_root_level: int
    logger_provider: LoggerProvider | None = None
    otlp_handler: logging.Handler | None = None
    _closed: bool = False

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        root = logging.getLogger()
        if self.otlp_handler is not None:
            root.removeHandler(self.otlp_handler)
        if self.logger_provider is not None:
            self.logger_provider.shutdown()
        root.removeHandler(self.console_handler)
        root.setLevel(self.previous_root_level)


def _build_otlp_pipeline(resource: Resource) -> tuple[LoggerProvider, LoggingHandler]:
    provider = LoggerProvider(resource=resource, shutdown_on_exit=False)
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    handler = _SafeOtelLoggingHandler(level=logging.INFO, logger_provider=provider)
    handler.addFilter(_SkipExporterInternals())
    return provider, handler


def configure_logging(
    *,
    service_name: str,
    environment: str,
    worker_name: str | None = None,
    console_stream: TextIO | None = None,
) -> ObservabilityRuntime:
    """Configura console JSON e, quando completo, o envio OTLP em lote."""

    service_version = _service_version()
    root = logging.getLogger()
    previous_root_level = root.level
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)

    console_handler = logging.StreamHandler(console_stream or sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(StructuredJsonFormatter(
        service_name=service_name,
        environment=environment,
        service_version=service_version,
        worker_name=worker_name,
    ))
    root.addHandler(console_handler)

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    headers = os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "").strip()
    provider: LoggerProvider | None = None
    otlp_handler: LoggingHandler | None = None
    if endpoint and headers:
        attributes = {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
        }
        if worker_name:
            attributes["worker.name"] = worker_name
        try:
            provider, otlp_handler = _build_otlp_pipeline(Resource.create(attributes))
        except Exception as error:
            logging.getLogger(__name__).warning(
                "Exportacao OTLP desabilitada por configuracao invalida: %s",
                type(error).__name__,
                extra={"operation": "otel_setup", "status": "disabled"},
            )
        else:
            root.addHandler(otlp_handler)
    elif endpoint or headers:
        logging.getLogger(__name__).warning(
            "Exportacao OTLP desabilitada: endpoint e headers devem ser configurados juntos",
            extra={"operation": "otel_setup", "status": "disabled"},
        )

    return ObservabilityRuntime(
        service_name=service_name,
        otlp_enabled=otlp_handler is not None,
        console_handler=console_handler,
        previous_root_level=previous_root_level,
        logger_provider=provider,
        otlp_handler=otlp_handler,
    )
