"""Cliente restrito para o servidor oficial MCP Fetch.

O servidor de referência aceita URLs arbitrárias e, por isso, pode alcançar
endereços internos. Este adaptador aplica uma allowlist antes de iniciar o MCP e
não repassa credenciais da aplicação ao subprocesso.
"""

import asyncio
import ipaddress
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

from app.core import config


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRUNCATION_PATTERN = re.compile(
    r"<error>Content truncated\. Call the fetch tool with a start_index of "
    r"(\d+) to get more content\.</error>\s*$"
)


def _allowed_domains() -> tuple[str, ...]:
    configured = config.FETCH_MCP_ALLOWED_DOMAINS or "gov.br"
    return tuple({
        domain.strip().lower().rstrip(".")
        for domain in configured.split(",")
        if domain.strip()
    })


def _validate_public_url(url: str) -> str:
    if not isinstance(url, str) or not 1 <= len(url) <= 2048:
        raise ValueError("URL inválida.")
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if (
        parsed.scheme != "https"
        or not hostname
        or literal_ip is not None
        or hostname == "localhost"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
    ):
        raise ValueError("Somente URLs HTTPS públicas e sem credenciais são permitidas.")
    if not any(hostname == domain or hostname.endswith("." + domain)
               for domain in _allowed_domains()):
        raise ValueError("Domínio não autorizado para consulta externa.")
    return url


class FetchMCPClient:
    """Executa somente a tool `fetch` em domínios previamente autorizados."""

    def __init__(self, timeout: float = 25):
        self.timeout = timeout
        self._cache: dict[tuple, tuple[float, dict]] = {}
        self._cache_lock = Lock()

    async def fetch(
        self,
        url: str,
        *,
        max_length: int = 12000,
        start_index: int = 0,
        raw: bool = False,
    ) -> dict:
        try:
            safe_url = _validate_public_url(url)
        except (TypeError, ValueError):
            return {"status": "nao_autorizado", "mensagem": "Fonte externa não autorizada."}
        if not 1 <= max_length <= 50000 or not 0 <= start_index <= 500000:
            return {"status": "erro", "mensagem": "Limites da consulta externa inválidos."}

        cache_key = (safe_url, max_length, start_index, raw)
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached and monotonic() - cached[0] <= config.FETCH_MCP_CACHE_TTL_SECONDS:
                return deepcopy(cached[1])

        child_environment = get_default_environment()
        child_environment["PYTHONUTF8"] = "1"
        child_environment["PYTHONIOENCODING"] = "utf-8"
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_server_fetch"],
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
                        if "fetch" not in {item.name for item in tools.tools}:
                            return {
                                "status": "indisponivel",
                                "mensagem": "MCP Fetch indisponível.",
                            }
                        response = await session.call_tool("fetch", arguments={
                            "url": safe_url,
                            "max_length": max_length,
                            "start_index": start_index,
                            "raw": raw,
                        })
        except Exception:
            return {
                "status": "indisponivel",
                "mensagem": "Consulta à fonte externa indisponível.",
            }
        if response.isError:
            return {
                "status": "indisponivel",
                "mensagem": "Não foi possível consultar a fonte externa.",
            }

        text = next(
            (
                item.text
                for item in response.content
                if isinstance(getattr(item, "text", None), str)
            ),
            "",
        )
        if not text:
            structured = response.structuredContent
            if isinstance(structured, dict):
                text = json.dumps(structured, ensure_ascii=False)
        prefix = f"Contents of {safe_url}:\n"
        if text.startswith(prefix):
            text = text[len(prefix):]
        truncation = TRUNCATION_PATTERN.search(text)
        next_index = int(truncation.group(1)) if truncation else None
        if truncation:
            text = text[:truncation.start()].rstrip()
        if not text or text.startswith("<error>"):
            return {
                "status": "indisponivel",
                "mensagem": "A fonte externa não retornou conteúdo utilizável.",
            }
        result = {
            "status": "ok",
            "url": safe_url,
            "conteudo": text,
            "truncado": next_index is not None,
            "proximo_indice": next_index,
            "protocolo": "MCP",
        }
        with self._cache_lock:
            self._cache[cache_key] = (monotonic(), deepcopy(result))
        return result


_default_client = FetchMCPClient()


def get_fetch_mcp_client() -> FetchMCPClient:
    return _default_client
