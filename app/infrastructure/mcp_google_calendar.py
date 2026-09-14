import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

from app.core import config


MCP_TOOL_ALLOWLIST = frozenset({
    "google_calendar_status",
    "google_calendar_list_events",
    "google_calendar_create_event",
})
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class GoogleCalendarMCPClient:
    """Cliente do servidor MCP interno; não expõe ferramentas arbitrárias ao LLM."""

    def __init__(self, timeout: float = 30):
        self.timeout = timeout

    async def call_tool(self, name: str, arguments: dict) -> dict:
        if name not in MCP_TOOL_ALLOWLIST:
            return {"status": "erro", "mensagem": "Ferramenta MCP não autorizada."}
        child_environment = get_default_environment()
        child_environment["PYTHONUTF8"] = "1"
        optional_secrets = {
            "MONGODB_URI": config.MONGODB_URI,
            "MONGODB_DATABASE": config.MONGODB_DATABASE,
            "GOOGLE_OAUTH_CLIENT_ID": config.GOOGLE_OAUTH_CLIENT_ID,
            "GOOGLE_OAUTH_CLIENT_SECRET": config.GOOGLE_OAUTH_CLIENT_SECRET,
            "GOOGLE_OAUTH_REDIRECT_URI": config.GOOGLE_OAUTH_REDIRECT_URI,
            "GOOGLE_OAUTH_TOKEN_ENCRYPTION_KEY": config.GOOGLE_OAUTH_TOKEN_ENCRYPTION_KEY,
        }
        child_environment.update({
            key: value for key, value in optional_secrets.items() if value
        })
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp.google_calendar_server"],
            env=child_environment,
            cwd=PROJECT_ROOT,
            encoding="utf-8",
            encoding_error_handler="replace",
        )
        try:
            async with asyncio.timeout(self.timeout):
                async with stdio_client(parameters) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        if name not in {item.name for item in tools.tools}:
                            return {
                                "status": "indisponivel",
                                "mensagem": "Ferramenta do Google Calendar indisponível.",
                            }
                        response = await session.call_tool(name, arguments=arguments)
        except Exception:
            return {
                "status": "indisponivel",
                "mensagem": "Integração MCP com Google Calendar indisponível.",
            }
        if response.isError:
            return {
                "status": "indisponivel",
                "mensagem": "Integração MCP com Google Calendar indisponível.",
            }
        structured = response.structuredContent
        if isinstance(structured, dict):
            result = structured.get("result", structured)
            if isinstance(result, dict):
                return result
        for content in response.content:
            text = getattr(content, "text", None)
            if not isinstance(text, str):
                continue
            try:
                result = json.loads(text)
            except ValueError:
                continue
            if isinstance(result, dict):
                return result
        return {
            "status": "indisponivel",
            "mensagem": "Resposta inválida da integração Google Calendar.",
        }


_default_client = GoogleCalendarMCPClient()


def get_google_calendar_mcp_client() -> GoogleCalendarMCPClient:
    return _default_client
