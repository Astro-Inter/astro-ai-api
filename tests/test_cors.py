from fastapi.testclient import TestClient

from app.core import config
from app.main import create_app


ALLOWED_ORIGIN = "https://astro-inter.github.io"
LOCAL_ORIGIN = "http://localhost:4173"


def test_cors_preflight_allows_configured_frontend(monkeypatch):
    monkeypatch.setattr(config, "CORS_ALLOWED_ORIGINS", [ALLOWED_ORIGIN, LOCAL_ORIGIN])

    with TestClient(create_app()) as client:
        response = client.options(
            "/chat/messages?markdown=true",
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "POST" in response.headers["access-control-allow-methods"]
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers
    assert "content-type" in allowed_headers


def test_cors_preflight_rejects_unconfigured_origin(monkeypatch):
    monkeypatch.setattr(config, "CORS_ALLOWED_ORIGINS", [ALLOWED_ORIGIN])

    with TestClient(create_app()) as client:
        response = client.options(
            "/chat/messages",
            headers={
                "Origin": "https://site-nao-autorizado.example",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_can_allow_any_origin_explicitly(monkeypatch):
    monkeypatch.setattr(config, "CORS_ALLOWED_ORIGINS", ["*"])

    with TestClient(create_app()) as client:
        response = client.options(
            "/chat/messages",
            headers={
                "Origin": "https://qualquer-site.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers


def test_cors_origin_parser_accepts_exact_wildcard(monkeypatch):
    monkeypatch.setenv("CORS_TEST_ORIGINS", "*")

    assert config._cors_origins_env("CORS_TEST_ORIGINS") == ["*"]


def test_cors_origin_parser_ignores_unsafe_and_invalid_values(monkeypatch):
    monkeypatch.setenv(
        "CORS_TEST_ORIGINS",
        "*,null,https://astro-inter.github.io/,https://astro-inter.github.io,"
        "https://example.com/caminho,arquivo-local,http://localhost:4173",
    )

    assert config._cors_origins_env("CORS_TEST_ORIGINS") == [
        ALLOWED_ORIGIN,
        LOCAL_ORIGIN,
    ]
