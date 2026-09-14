import asyncio

import pytest

from app.core import config
from app.modules.shared import public_sources
from app.modules.shared.public_sources import (
    PUBLIC_SOURCES,
    consultar_fontes_publicas,
)
from app.modules.sst.tools import consultar_orientacoes_sst


@pytest.fixture(autouse=True)
def use_local_public_sources(monkeypatch):
    monkeypatch.setattr(config, "A2A_PUBLIC_RESEARCH_URL", "")


class FakeFetchClient:
    def __init__(self, contents=None):
        self.contents = contents or {}
        self.calls = []

    async def fetch(self, url, **kwargs):
        self.calls.append((url, kwargs))
        content = self.contents.get(url)
        if content is None:
            return {"status": "indisponivel"}
        return {"status": "ok", "conteudo": content, "url": url, "protocolo": "MCP"}


def test_public_sources_use_only_curated_urls_and_return_relevant_excerpt(monkeypatch):
    source = PUBLIC_SOURCES["fundacentro_publicacoes"]
    client = FakeFetchClient({
        source.url: (
            "# Publicações institucionais\n\n"
            "Atualizado em 17/06/2026\n\n"
            "Diretrizes para aplicar a NR-1 com a inclusão dos riscos psicossociais "
            "e analisar a organização do trabalho para intervir.\n\n"
            "Manual sobre outro assunto sem relação com a consulta."
        ),
    })
    monkeypatch.setattr(public_sources, "get_fetch_mcp_client", lambda: client)

    result = asyncio.run(consultar_fontes_publicas(
        "riscos psicossociais",
        area="sst_geral",
        fontes=["fundacentro_publicacoes"],
    ))

    assert result["status"] == "ok"
    assert result["protocolo"] == "MCP"
    assert result["fontes"][0]["orgao"] == "Fundacentro"
    assert result["fontes"][0]["atualizado_em"] == "17/06/2026"
    assert "riscos psicossociais" in result["fontes"][0]["trechos"][0]
    assert client.calls == [(source.url, {"max_length": 50000})]


def test_psychosocial_query_prioritizes_official_nr1_even_with_generic_mte_source(monkeypatch):
    nr1 = PUBLIC_SOURCES["mte_nr1"]
    generic = PUBLIC_SOURCES["mte_sst"]
    client = FakeFetchClient({
        nr1.url: (
            "# NR-1\n\nO MTE publicou o Guia de informações sobre Fatores de "
            "Riscos Psicossociais Relacionados ao Trabalho."
        ),
        generic.url: "# Segurança e Saúde no Trabalho\n\nPágina geral da fiscalização.",
    })
    monkeypatch.setattr(public_sources, "get_fetch_mcp_client", lambda: client)

    result = asyncio.run(public_sources._consultar_fontes_publicas_local(
        "Busque orientações do MTE sobre riscos psicossociais",
        area="sst_geral", fontes=["mte_sst"], limite_fontes=2,
    ))

    assert result["status"] == "ok"
    assert [item["id"] for item in result["fontes"]] == ["mte_nr1"]
    assert [url for url, _ in client.calls] == [nr1.url, generic.url]


def test_long_official_page_reads_next_chunk_when_navigation_has_no_match(monkeypatch):
    nr1 = PUBLIC_SOURCES["mte_nr1"]

    class PagedFetchClient:
        def __init__(self):
            self.calls = []

        async def fetch(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if kwargs.get("start_index") is None:
                return {
                    "status": "ok", "conteudo": "# Menu\n\nLinks gerais do governo.",
                    "truncado": True, "proximo_indice": 50000,
                }
            return {
                "status": "ok",
                "conteudo": (
                    "# NR-1\n\nGuia de informações sobre Fatores de Riscos "
                    "Psicossociais Relacionados ao Trabalho."
                ),
                "truncado": False, "proximo_indice": None,
            }

    client = PagedFetchClient()
    monkeypatch.setattr(public_sources, "get_fetch_mcp_client", lambda: client)

    result = asyncio.run(public_sources._consultar_fontes_publicas_local(
        "riscos psicossociais", area="sst_geral", fontes=["mte_nr1"],
    ))

    assert result["status"] == "ok"
    assert result["fontes"][0]["id"] == "mte_nr1"
    assert client.calls == [
        (nr1.url, {"max_length": 50000}),
        (nr1.url, {"max_length": 50000, "start_index": 50000}),
    ]


def test_public_sources_do_not_cross_area_allowlist(monkeypatch):
    client = FakeFetchClient()
    monkeypatch.setattr(public_sources, "get_fetch_mcp_client", lambda: client)

    result = asyncio.run(consultar_fontes_publicas(
        "legislação",
        area="faq_politicas",
        fontes=["mte_sst"],
    ))

    assert result["status"] == "nao_autorizado"
    assert client.calls == []


def test_sst_public_tool_has_no_free_url_and_uses_mcp_service(monkeypatch):
    source = PUBLIC_SOURCES["anvisa_manuais_saude"]
    client = FakeFetchClient({
        source.url: "# Manuais e Guias\n\nGuia atualizado sobre higiene das mãos em serviços de saúde.",
    })
    monkeypatch.setattr(public_sources, "get_fetch_mcp_client", lambda: client)

    result = asyncio.run(consultar_orientacoes_sst.ainvoke({
        "termo": "higiene das mãos",
        "fontes": ["anvisa_manuais_saude"],
    }))

    assert result["status"] == "ok"
    properties = consultar_orientacoes_sst.args_schema.model_json_schema()["properties"]
    assert "url" not in properties
    assert set(properties) == {"termo", "fontes"}
