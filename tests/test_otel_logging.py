import io
import json
import logging

from app.observability import logging as otel_logging


def test_console_logging_works_without_otel_environment(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)
    stream = io.StringIO()
    runtime = otel_logging.configure_logging(
        service_name="astro-ai-api",
        environment="test",
        console_stream=stream,
    )
    try:
        logging.getLogger("tests.observability").info(
            "Operacao concluida", extra={"operation": "test", "status": "success"},
        )
    finally:
        runtime.shutdown()

    payload = json.loads(stream.getvalue())
    assert runtime.otlp_enabled is False
    assert payload["level"] == "INFO"
    assert payload["service.name"] == "astro-ai-api"
    assert payload["environment"] == "test"
    assert payload["message"] == "Operacao concluida"
    assert payload["operation"] == "test"
    assert payload["status"] == "success"


def test_otlp_requires_endpoint_and_headers(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://example.test/otlp")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)
    runtime = otel_logging.configure_logging(
        service_name="astro-ai-api",
        environment="test",
        console_stream=io.StringIO(),
    )
    try:
        assert runtime.otlp_enabled is False
        assert runtime.logger_provider is None
    finally:
        runtime.shutdown()


def test_invalid_otlp_configuration_does_not_break_console(monkeypatch):
    def fail_pipeline(_resource):
        raise ValueError("invalid exporter")

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "invalid")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "Authorization=invalid")
    monkeypatch.setattr(otel_logging, "_build_otlp_pipeline", fail_pipeline)
    stream = io.StringIO()
    runtime = otel_logging.configure_logging(
        service_name="astro-ai-api",
        environment="test",
        console_stream=stream,
    )
    try:
        assert runtime.otlp_enabled is False
        assert "ValueError" in stream.getvalue()
        assert "Authorization=invalid" not in stream.getvalue()
    finally:
        runtime.shutdown()


def test_otlp_pipeline_receives_safe_resource_attributes(monkeypatch):
    captured = {}

    class FakeProvider:
        shutdown_called = False

        def shutdown(self):
            self.shutdown_called = True

    provider = FakeProvider()

    def fake_pipeline(resource):
        captured.update(resource.attributes)
        return provider, logging.NullHandler()

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://example.test/otlp")
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_HEADERS", "Authorization=Basic%20secret-value",
    )
    monkeypatch.setattr(otel_logging, "_build_otlp_pipeline", fake_pipeline)
    runtime = otel_logging.configure_logging(
        service_name="astro-ai-a2a",
        environment="production",
        worker_name="public-research",
        console_stream=io.StringIO(),
    )
    try:
        assert runtime.otlp_enabled is True
        assert captured["service.name"] == "astro-ai-a2a"
        assert captured["deployment.environment"] == "production"
        assert captured["worker.name"] == "public-research"
        assert "secret-value" not in repr(captured)
    finally:
        runtime.shutdown()
    assert provider.shutdown_called is True


def test_console_redacts_common_credentials(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)
    stream = io.StringIO()
    runtime = otel_logging.configure_logging(
        service_name="astro-ai-api",
        environment="test",
        console_stream=stream,
    )
    try:
        logging.getLogger("tests.observability").error(
            "authorization=Basic super-secret password=hunter2",
        )
    finally:
        runtime.shutdown()

    output = stream.getvalue()
    assert "super-secret" not in output
    assert "hunter2" not in output
    assert output.count("[REDACTED]") == 2


def test_otel_handler_redacts_message_and_exception():
    handler = otel_logging._SafeOtelLoggingHandler()
    try:
        raise RuntimeError("token=exception-value")
    except RuntimeError:
        record = logging.getLogger("tests.observability").makeRecord(
            "tests.observability",
            logging.ERROR,
            __file__,
            1,
            "authorization=Basic message-value",
            (),
            __import__("sys").exc_info(),
        )
        record.api_key = "attribute-value"
        record.error = "credentials=error-value"

    translated = handler._translate(record)
    assert "message-value" not in translated.body
    assert "exception-value" not in translated.attributes["exception.stacktrace"]
    assert translated.attributes["exception.type"] == "RuntimeError"
    assert translated.attributes["api_key"] == "[REDACTED]"
    assert "error-value" not in translated.attributes["error"]
