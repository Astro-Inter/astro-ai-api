import asyncio

from app.core import config
from app.infrastructure.mcp_fetch import FetchMCPClient, _validate_public_url


def test_fetch_mcp_accepts_only_allowlisted_https_domains(monkeypatch):
    monkeypatch.setattr(config, "FETCH_MCP_ALLOWED_DOMAINS", "gov.br")

    assert _validate_public_url("https://www.gov.br/pagina") == "https://www.gov.br/pagina"
    for url in (
        "http://www.gov.br/pagina",
        "https://127.0.0.1/segredo",
        "https://evilgov.br/pagina",
        "https://usuario:senha@www.gov.br/pagina",
        "https://www.gov.br:8443/pagina",
    ):
        try:
            _validate_public_url(url)
        except ValueError:
            pass
        else:
            raise AssertionError(f"URL deveria ter sido bloqueada: {url}")


def test_fetch_mcp_blocks_url_before_starting_server(monkeypatch):
    monkeypatch.setattr(config, "FETCH_MCP_ALLOWED_DOMAINS", "gov.br")

    result = asyncio.run(FetchMCPClient().fetch("https://localhost/admin"))

    assert result == {
        "status": "nao_autorizado",
        "mensagem": "Fonte externa não autorizada.",
    }
