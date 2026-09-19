import asyncio
from datetime import datetime

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.api import auth
from app.core import config
from app.core.security import CurrentUser
from app.infrastructure.google_calendar import TokenCipher
from app.infrastructure.mcp_google_calendar import GoogleCalendarMCPClient
from app.main import create_app
from app.modules.agenda import tools as agenda_tools
from app.modules.chat.graph import _confirmacao_explicita


class FakeMCPClient:
    def __init__(self, *, connected=False):
        self.connected = connected
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "google_calendar_status":
            return {"status": "ok", "conectado": self.connected, "escopos": []}
        if name == "google_calendar_list_events":
            if not self.connected:
                return {
                    "status": "conexao_necessaria",
                    "mensagem": "Conecte sua conta Google Calendar para continuar.",
                    "rota_conexao": "/integracoes/google-calendar/conectar",
                }
            return {"status": "sem_dados", "eventos": []}
        if name == "google_calendar_create_event":
            if not self.connected:
                return {
                    "status": "conexao_necessaria",
                    "rota_conexao": "/integracoes/google-calendar/conectar",
                }
            return {
                "status": "ok",
                "evento": {
                    "id": arguments["id_evento"],
                    "titulo": arguments["titulo"],
                    "inicio": arguments["inicio"],
                    "fim": arguments["fim"],
                    "link": "https://calendar.google.com/event?test",
                },
            }
        raise AssertionError(name)


def tool_config(*, pending=None, explicit=False):
    return {"configurable": {
        "usuario_atual": {"uid": "user-a", "role": "COLABORADOR"},
        "session_id": "session-a",
        "acao_pendente": pending,
        "confirmacao_explicita": explicit,
        "fuso": "America/Sao_Paulo",
    }}


def test_calendar_connection_is_only_requested_when_calendar_tool_is_used(monkeypatch):
    client = FakeMCPClient(connected=False)
    monkeypatch.setattr(agenda_tools, "get_google_calendar_mcp_client", lambda: client)
    start = datetime.fromisoformat("2026-09-20T08:00:00-03:00")
    end = datetime.fromisoformat("2026-09-20T12:00:00-03:00")

    result = asyncio.run(agenda_tools.criar_evento_google_calendar.ainvoke(
        {
            "titulo": "Treinamento NR-12",
            "inicio": start,
            "fim": end,
            "descricao": "Treinamento atribuído no Astro.",
            "confirmar": False,
        },
        config=tool_config(),
    ))

    assert result["status"] == "conexao_necessaria"
    assert result["rota_conexao"] == "/integracoes/google-calendar/conectar"
    assert result["acao_pendente"]["tipo"] == "criar_evento_google_calendar"
    assert [call[0] for call in client.calls] == ["google_calendar_status"]


def test_calendar_event_requires_exact_pending_preview_and_confirmation(monkeypatch):
    client = FakeMCPClient(connected=True)
    monkeypatch.setattr(agenda_tools, "get_google_calendar_mcp_client", lambda: client)
    start = datetime.fromisoformat("2026-09-20T08:00:00-03:00")
    end = datetime.fromisoformat("2026-09-20T12:00:00-03:00")
    values = {
        "titulo": "Treinamento NR-12",
        "inicio": start,
        "fim": end,
        "descricao": "Treinamento atribuído no Astro.",
    }
    preview = asyncio.run(agenda_tools.criar_evento_google_calendar.ainvoke(
        {**values, "confirmar": False}, config=tool_config(),
    ))
    assert preview["status"] == "aguardando_confirmacao"

    invalid = asyncio.run(agenda_tools.criar_evento_google_calendar.ainvoke(
        {**values, "titulo": "Outro título", "confirmar": True},
        config=tool_config(pending=preview["acao_pendente"], explicit=True),
    ))
    assert invalid["status"] == "confirmacao_invalida"

    result = asyncio.run(agenda_tools.criar_evento_google_calendar.ainvoke(
        {**values, "confirmar": True},
        config=tool_config(pending=preview["acao_pendente"], explicit=True),
    ))
    assert result["status"] == "ok"
    assert result["evento"]["titulo"] == "Treinamento NR-12"
    create_call = client.calls[-1]
    assert create_call[0] == "google_calendar_create_event"
    assert create_call[1]["firebase_uid"] == "user-a"
    assert create_call[1]["id_evento"].startswith("astro")


def test_mcp_client_blocks_tools_outside_allowlist():
    result = asyncio.run(
        GoogleCalendarMCPClient().call_tool("apagar_todos_eventos", {})
    )
    assert result["status"] == "erro"


def test_natural_calendar_confirmations_are_recognized_without_loose_matching():
    for message in (
        "Sim, pode criar.",
        "Sim, pode criar o evento no meu Google Calendar",
        "Sim, eu confirmo",
        "Confirmo a criação",
    ):
        assert _confirmacao_explicita(message) is True
    assert _confirmacao_explicita("Sim, mas não crie") is False


def test_token_cipher_does_not_store_plaintext():
    cipher = TokenCipher(Fernet.generate_key().decode())
    encrypted = cipher.encrypt("refresh-secret")
    assert encrypted != "refresh-secret"
    assert cipher.decrypt(encrypted) == "refresh-secret"


class FakeOAuthService:
    def __init__(self):
        self.calls = []

    async def status(self, uid):
        self.calls.append(("status", uid))
        return {"conectado": False, "escopos": []}

    async def connection_url(self, uid, redirect_uri):
        self.calls.append(("connect", uid, redirect_uri))
        return "https://accounts.google.com/o/oauth2/v2/auth?state=test"

    async def callback(self, code, state):
        self.calls.append(("callback", code, state))
        return {"status": "conectado"}

    async def disconnect(self, uid):
        self.calls.append(("disconnect", uid))

    async def close(self):
        pass


def test_optional_oauth_routes(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_OAUTH_REDIRECT_URI", None)
    application = create_app()
    service = FakeOAuthService()
    application.state.google_calendar_oauth = service
    application.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(
        uid="user-a", role="COLABORADOR",
    )

    with TestClient(application) as client:
        status_response = client.get("/integracoes/google-calendar/status")
        assert status_response.status_code == 200
        assert status_response.json() == {"conectado": False, "escopos": []}
        assert status_response.headers["cache-control"] == "no-store"

        connect_response = client.get("/integracoes/google-calendar/conectar")
        assert connect_response.status_code == 200
        assert connect_response.json()["authorization_url"].startswith(
            "https://accounts.google.com/"
        )

        callback_response = client.get(
            "/integracoes/google-calendar/callback?code=code-a&state=state-a"
        )
        assert callback_response.status_code == 200
        assert callback_response.json()["status"] == "conectado"

        disconnect_response = client.delete("/integracoes/google-calendar")
        assert disconnect_response.status_code == 204

    assert [call[0] for call in service.calls] == [
        "status", "connect", "callback", "disconnect",
    ]
