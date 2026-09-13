"""Servidor MCP interno e restrito para operações no Google Calendar.

Execute diretamente apenas para diagnóstico:
    python -m app.mcp.google_calendar_server

O processo usa transporte stdio e não aceita conexões de rede externas.
"""

from mcp.server.fastmcp import FastMCP

from app.infrastructure.google_calendar import (
    GoogleCalendarAPI,
    GoogleCalendarConfigurationError,
    GoogleCalendarError,
    GoogleCalendarNotConnected,
    GoogleCalendarOAuthService,
)


mcp = FastMCP(
    "astro-google-calendar",
    instructions=(
        "Ferramentas internas do Astro para consultar e criar eventos no calendário "
        "principal do usuário autenticado."
    ),
    log_level="WARNING",
)


def _public_error(error: GoogleCalendarError) -> dict:
    if isinstance(error, GoogleCalendarNotConnected):
        return {
            "status": "conexao_necessaria",
            "mensagem": "Conecte sua conta Google Calendar para continuar.",
            "rota_conexao": "/integracoes/google-calendar/conectar",
        }
    if isinstance(error, GoogleCalendarConfigurationError):
        return {"status": "indisponivel", "mensagem": str(error)}
    return {
        "status": "indisponivel",
        "mensagem": "Google Calendar indisponível no momento.",
    }


@mcp.tool()
async def google_calendar_status(firebase_uid: str) -> dict:
    """Informa se o usuário autenticado já conectou seu Google Calendar."""
    service = GoogleCalendarOAuthService()
    try:
        status = await service.status(firebase_uid)
        return {"status": "ok", **status}
    except GoogleCalendarError as error:
        return _public_error(error)
    finally:
        await service.close()


@mcp.tool()
async def google_calendar_list_events(
    firebase_uid: str, inicio: str, fim: str, limite: int = 10,
) -> dict:
    """Lista eventos do calendário principal em um intervalo RFC 3339."""
    api = GoogleCalendarAPI()
    try:
        return await api.list_events(firebase_uid, inicio, fim, max(1, min(limite, 20)))
    except GoogleCalendarError as error:
        return _public_error(error)
    finally:
        await api.close()


@mcp.tool()
async def google_calendar_create_event(
    firebase_uid: str,
    id_evento: str,
    titulo: str,
    inicio: str,
    fim: str,
    fuso: str,
    descricao: str | None = None,
) -> dict:
    """Cria um evento no calendário principal depois da confirmação do Astro."""
    api = GoogleCalendarAPI()
    try:
        return await api.create_event(
            firebase_uid,
            event_id=id_evento,
            title=titulo,
            start=inicio,
            end=fim,
            timezone_name=fuso,
            description=descricao,
        )
    except GoogleCalendarError as error:
        return _public_error(error)
    finally:
        await api.close()


if __name__ == "__main__":
    mcp.run(transport="stdio")
