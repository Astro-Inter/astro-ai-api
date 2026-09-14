import json
import logging
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from firebase_admin import auth as firebase_auth

from app.api import auth
from app.core import config
from app.infrastructure.database.access import AccessLookupError
from app.main import create_app


PASSWORD = "fake-password-for-tests"
TOKEN = "fake-firebase-token-for-tests"
KEY = "fake-api-key-for-tests"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "test")
    monkeypatch.setattr(config, "ENABLE_DEV_LOGIN", True)
    monkeypatch.setattr(config, "FIREBASE_WEB_API_KEY", KEY)
    application = create_app()
    application.state.access_roles = Mock(
        get_role=AsyncMock(return_value="GESTOR"),
    )

    @application.get("/protected-for-tests")
    def protected(user: auth.CurrentUserDependency):
        return user

    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def firebase_http(monkeypatch):
    original_client = httpx.AsyncClient

    def configure(handler):
        def factory(**kwargs):
            assert kwargs["timeout"] == 10.0
            assert kwargs["follow_redirects"] is False
            return original_client(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)

    return configure


def post_login(client):
    return client.post("/auth/login", json={"email": "user@example.com", "password": PASSWORD})


def test_login_success_and_bearer(client, firebase_http, monkeypatch, caplog):
    def handler(request):
        assert request.url.path == "/v1/accounts:signInWithPassword"
        assert not request.url.query
        assert request.headers["x-goog-api-key"] == KEY
        assert json.loads(request.content) == {
            "email": "user@example.com", "password": PASSWORD, "returnSecureToken": True,
        }
        return httpx.Response(200, json={
            "idToken": TOKEN, "expiresIn": "3600", "refreshToken": "fake-refresh-token",
        })

    firebase_http(handler)
    caplog.set_level(logging.DEBUG)
    result = post_login(client)
    assert result.status_code == 200
    assert result.json() == {"access_token": TOKEN, "expires_in": 3600, "token_type": "Bearer"}
    assert result.headers["cache-control"] == "no-store"
    verify = Mock(return_value={"uid": "user-123", "role": "ignored"})
    monkeypatch.setattr(auth, "verify_firebase_id_token", verify)
    result = client.get("/protected-for-tests", headers={"Authorization": f"Bearer {TOKEN}"})
    assert result.json() == {"uid": "user-123", "role": "GESTOR"}
    verify.assert_called_once_with(TOKEN)
    client.app.state.access_roles.get_role.assert_awaited_once_with("user-123")
    for secret in (PASSWORD, TOKEN, KEY, "fake-refresh-token"):
        assert secret not in caplog.text


@pytest.mark.parametrize("code", [
    "EMAIL_NOT_FOUND", "INVALID_PASSWORD", "INVALID_LOGIN_CREDENTIALS", "USER_DISABLED",
])
def test_login_invalid_credentials(client, firebase_http, caplog, code):
    caplog.set_level(logging.DEBUG)
    firebase_http(lambda request: httpx.Response(400, json={"error": {"message": code}}))
    result = post_login(client)
    assert result.status_code == 401
    assert result.json() == {"detail": "Email ou senha invalidos."}
    assert PASSWORD not in caplog.text and KEY not in caplog.text


@pytest.mark.parametrize("env,enabled", [("test", False), ("production", True), ("staging", True)])
def test_login_hidden(monkeypatch, env, enabled):
    monkeypatch.setattr(config, "APP_ENV", env)
    monkeypatch.setattr(config, "ENABLE_DEV_LOGIN", enabled)
    with TestClient(create_app()) as client:
        assert client.post("/auth/login", json={}).status_code == 404
        assert "/auth/login" not in client.get("/openapi.json").json()["paths"]
        assert client.get("/health").json() == {"status": "ok"}


def test_missing_key(client, monkeypatch, firebase_http):
    monkeypatch.setattr(config, "FIREBASE_WEB_API_KEY", "")
    firebase_http(lambda request: pytest.fail("No request should be sent without an API key"))
    assert post_login(client).status_code == 503


@pytest.mark.parametrize("failure", ["timeout", "server", "malformed", "missing_token", "bad_expiry"])
def test_upstream_failure(client, firebase_http, caplog, failure):
    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout(PASSWORD, request=request)
        if failure == "server":
            return httpx.Response(500, text=PASSWORD)
        if failure == "malformed":
            return httpx.Response(200, text=PASSWORD)
        if failure == "missing_token":
            return httpx.Response(200, json={"expiresIn": "3600"})
        return httpx.Response(200, json={"idToken": TOKEN, "expiresIn": "invalid"})

    firebase_http(handler)
    caplog.set_level(logging.DEBUG)
    result = post_login(client)
    assert result.status_code == 503
    assert result.json() == {"detail": "Servico de autenticacao indisponivel."}
    assert PASSWORD not in caplog.text and TOKEN not in caplog.text and KEY not in caplog.text


@pytest.mark.parametrize("first_failure", ["disconnect", "server"])
def test_transient_firebase_failure_is_retried_once(
    client, firebase_http, caplog, first_failure,
):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            if first_failure == "disconnect":
                raise httpx.RemoteProtocolError(PASSWORD)
            return httpx.Response(503, text=PASSWORD)
        return httpx.Response(200, json={"idToken": TOKEN, "expiresIn": "3600"})

    firebase_http(handler)
    caplog.set_level(logging.DEBUG)
    result = post_login(client)

    assert result.status_code == 200
    assert result.json()["access_token"] == TOKEN
    assert calls == 2
    assert PASSWORD not in caplog.text and TOKEN not in caplog.text and KEY not in caplog.text


@pytest.mark.parametrize("payload", [{"password": PASSWORD}, {"email": "x", "password": PASSWORD}])
def test_validation_does_not_echo_password(client, payload):
    result = client.post("/auth/login", json=payload)
    assert result.status_code == 422
    assert PASSWORD not in result.text


def test_invalid_json_does_not_echo_password(client):
    result = client.post("/auth/login", content='{"password":"' + PASSWORD,
                         headers={"Content-Type": "application/json"})
    assert result.status_code == 422
    assert PASSWORD not in result.text


@pytest.mark.parametrize("headers", [{}, {"X-Dev-Auth-Token": "old-bypass"}, {"Authorization": "Basic abc"}])
def test_bearer_required(client, monkeypatch, headers):
    verify = Mock()
    monkeypatch.setattr(auth, "verify_firebase_id_token", verify)
    result = client.get("/protected-for-tests", headers=headers)
    assert result.status_code == 401
    assert result.headers["www-authenticate"] == "Bearer"
    verify.assert_not_called()


@pytest.mark.parametrize("error", [
    firebase_auth.InvalidIdTokenError("invalid"),
    firebase_auth.ExpiredIdTokenError("expired", cause=None),
    firebase_auth.RevokedIdTokenError("revoked"),
    firebase_auth.UserDisabledError("disabled"),
])
def test_rejected_firebase_token(client, monkeypatch, error):
    monkeypatch.setattr(auth, "verify_firebase_id_token", Mock(side_effect=error))
    assert client.get("/protected-for-tests", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 401


def test_admin_checks_revocation(monkeypatch):
    from app.infrastructure import firebase

    app = object()
    monkeypatch.setattr(firebase, "get_firebase_app", lambda: app)
    verify = Mock(return_value={"uid": "user-123"})
    monkeypatch.setattr(firebase.auth, "verify_id_token", verify)
    assert firebase.verify_firebase_id_token(TOKEN) == {"uid": "user-123"}
    verify.assert_called_once_with(TOKEN, app=app, check_revoked=True)


@pytest.mark.parametrize("lookup,status_code,detail", [
    (None, 403, "Usuario sem acesso ao Astro."),
    (AccessLookupError(), 503, "Servico de autorizacao indisponivel."),
])
def test_database_role_is_required(client, monkeypatch, lookup, status_code, detail):
    monkeypatch.setattr(auth, "verify_firebase_id_token", Mock(return_value={"uid": "user-123"}))
    if isinstance(lookup, Exception):
        client.app.state.access_roles.get_role.side_effect = lookup
    else:
        client.app.state.access_roles.get_role.return_value = lookup
    result = client.get("/protected-for-tests", headers={"Authorization": f"Bearer {TOKEN}"})
    assert result.status_code == status_code
    assert result.json() == {"detail": detail}
