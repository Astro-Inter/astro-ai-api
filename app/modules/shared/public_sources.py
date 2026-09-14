"""Consulta restrita a catálogos públicos oficiais por MCP.

As URLs ficam catalogadas no backend. O usuário e o modelo escolhem apenas uma
fonte conhecida, nunca fornecem uma URL livre para o servidor MCP Fetch.
"""

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from app.infrastructure.mcp_fetch import get_fetch_mcp_client
from app.core import config


PublicSourceId = Literal[
    "mte_sst",
    "mte_nr1",
    "mte_legislacao_sst",
    "mte_manuais",
    "fundacentro_publicacoes",
    "anvisa_manuais_saude",
    "anvisa_legislacao_saude",
]
PublicSourceArea = Literal["faq_politicas", "sst_geral"]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublicSource:
    titulo: str
    orgao: str
    url: str


PUBLIC_SOURCES: dict[PublicSourceId, PublicSource] = {
    "mte_sst": PublicSource(
        titulo="Segurança e Saúde no Trabalho",
        orgao="Ministério do Trabalho e Emprego",
        url=(
            "https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/"
            "seguranca-e-saude-no-trabalho"
        ),
    ),
    "mte_nr1": PublicSource(
        titulo="NR-1 e orientações sobre riscos psicossociais",
        orgao="Ministério do Trabalho e Emprego",
        url=(
            "https://www.gov.br/trabalho-e-emprego/pt-br/acesso-a-informacao/"
            "participacao-social/conselhos-e-orgaos-colegiados/"
            "comissao-tripartite-partitaria-permanente/normas-regulamentadora/"
            "normas-regulamentadoras-vigentes/nr-1"
        ),
    ),
    "mte_legislacao_sst": PublicSource(
        titulo="Legislação de Segurança e Saúde no Trabalho",
        orgao="Ministério do Trabalho e Emprego",
        url=(
            "https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/"
            "seguranca-e-saude-no-trabalho/legislacao_de_sst"
        ),
    ),
    "mte_manuais": PublicSource(
        titulo="Manuais e Publicações da Inspeção do Trabalho",
        orgao="Ministério do Trabalho e Emprego",
        url=(
            "https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/"
            "manuais-e-publicacoes"
        ),
    ),
    "fundacentro_publicacoes": PublicSource(
        titulo="Publicações institucionais da Fundacentro",
        orgao="Fundacentro",
        url=(
            "https://www.gov.br/fundacentro/pt-br/centrais-de-conteudo/biblioteca/"
            "publicacoes-institucionais"
        ),
    ),
    "anvisa_manuais_saude": PublicSource(
        titulo="Manuais e Guias para Serviços de Saúde",
        orgao="Anvisa",
        url=(
            "https://www.gov.br/anvisa/pt-br/centraisdeconteudo/publicacoes/"
            "servicosdesaude/manuais"
        ),
    ),
    "anvisa_legislacao_saude": PublicSource(
        titulo="Legislação de Segurança do Paciente",
        orgao="Anvisa",
        url=(
            "https://www.gov.br/anvisa/pt-br/assuntos/servicosdesaude/"
            "seguranca-do-paciente/legislacao"
        ),
    ),
}

AREA_SOURCES: dict[PublicSourceArea, tuple[PublicSourceId, ...]] = {
    "faq_politicas": (
        "mte_legislacao_sst",
        "mte_manuais",
        "fundacentro_publicacoes",
        "anvisa_legislacao_saude",
    ),
    "sst_geral": (
        "mte_sst",
        "mte_manuais",
        "fundacentro_publicacoes",
        "anvisa_manuais_saude",
        "mte_nr1",
    ),
}

_STOP_WORDS = frozenset({
    "a", "ao", "aos", "as", "com", "como", "da", "das", "de", "do", "dos",
    "e", "em", "eu", "me", "meu", "minha", "na", "nas", "no", "nos", "o",
    "os", "para", "por", "qual", "quais", "que", "sobre", "um", "uma",
    "busca", "buscar", "busque", "encontre", "orientacao", "orientacoes",
    "informacao", "informacoes", "mte", "ministerio", "trabalho", "emprego",
    "fundacentro", "anvisa", "cartilha", "cartilhas", "guia", "guias",
    "manual", "manuais", "oficial", "oficiais",
})
_BOILERPLATE = (
    "compartilhe por facebook",
    "todo o conteudo deste site",
    "voce esta aqui",
    "voltar ao topo",
    "acesso a informacao",
)


def _normalizar(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(
        character for character in decomposed
        if not unicodedata.combining(character)
    )


def _termos_relevantes(query: str) -> tuple[str, ...]:
    tokens = re.findall(r"[a-z0-9]+", _normalizar(query))
    return tuple(dict.fromkeys(
        token for token in tokens if len(token) >= 3 and token not in _STOP_WORDS
    ))


def _limpar_bloco(block: str) -> str:
    block = re.sub(r"!\[[^]]*]\([^)]*\)", " ", block)
    block = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", block)
    block = re.sub(r"<[^>]+>", " ", block)
    block = re.sub(r"^[#>*+\-\s]+", "", block)
    return re.sub(r"\s+", " ", block).strip()


def _extrair_trechos(content: str, query: str, *, limit: int = 1) -> list[str]:
    terms = _termos_relevantes(query)
    if not terms:
        return []
    normalized_query = _normalizar(query).strip()
    candidates = []
    seen = set()
    for position, raw_block in enumerate(re.split(r"\n\s*\n|(?=^#{1,4}\s)", content, flags=re.M)):
        clean = _limpar_bloco(raw_block)
        if len(clean) < 25:
            continue
        normalized = _normalizar(clean)
        if any(marker in normalized for marker in _BOILERPLATE):
            continue
        if "psicossoc" in normalized_query and "psicossoc" not in normalized:
            continue
        matched = sum(1 for term in terms if term in normalized)
        if not matched:
            continue
        score = matched * 3
        if normalized_query and normalized_query in normalized:
            score += 8
        if any(marker in normalized for marker in ("manual", "guia", "cartilha", "lei", "portaria", "rdc")):
            score += 1
        excerpt = clean[:600].rstrip(" ,;:-")
        fingerprint = _normalizar(excerpt)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        candidates.append((score, -position, excerpt))
    candidates.sort(reverse=True)
    return [excerpt for _, _, excerpt in candidates[:limit]]


def _data_atualizacao(content: str) -> str | None:
    match = re.search(
        r"(?:Atualizado|Modificado)\s+em\s+(\d{2}/\d{2}/\d{4})",
        content,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def _selecionar_fontes(
    termo: str,
    *,
    area: PublicSourceArea,
    fontes: list[PublicSourceId] | None,
    limite_fontes: int,
) -> list[PublicSourceId]:
    allowed = AREA_SOURCES[area]
    # A NR-1 contém o guia oficial, mas a página geral de SST não o reproduz.
    # A expansão é restrita a uma URL fixa do MTE e respeita a área autorizada.
    requested = list(dict.fromkeys(fontes or allowed))
    normalized = _normalizar(termo)
    if (area == "sst_geral" and "psicossoc" in normalized
            and (not fontes or any(source.startswith("mte_") for source in fontes))):
        requested = ["mte_nr1", *requested]
    return list(dict.fromkeys(
        source for source in requested if source in allowed
    ))[:limite_fontes]


async def _buscar_conteudo(client, url: str, termo: str) -> dict:
    """Lê até dois blocos; páginas gov.br podem começar com um menu extenso."""
    response = await client.fetch(url, max_length=50000)
    if response.get("status") != "ok":
        return response
    content = response.get("conteudo", "")
    next_index = response.get("proximo_indice")
    if (response.get("truncado") and isinstance(next_index, int)
            and next_index > 0 and not _extrair_trechos(content, termo)):
        continuation = await client.fetch(
            url, max_length=50000, start_index=next_index,
        )
        if continuation.get("status") == "ok":
            content = f"{content}\n\n{continuation.get('conteudo', '')}"
    return {**response, "conteudo": content}


async def _consultar_fontes_publicas_local(
    termo: str,
    *,
    area: PublicSourceArea,
    fontes: list[PublicSourceId] | None = None,
    limite_fontes: int = 4,
) -> dict:
    """Busca trechos relevantes em fontes oficiais previamente cadastradas."""
    selected = _selecionar_fontes(
        termo, area=area, fontes=fontes, limite_fontes=limite_fontes,
    )
    if not selected:
        return {
            "status": "nao_autorizado",
            "mensagem": "Nenhuma fonte autorizada foi selecionada.",
            "fontes": [],
        }

    client = get_fetch_mcp_client()
    fetched = await asyncio.gather(*(
        _buscar_conteudo(client, PUBLIC_SOURCES[source_id].url, termo)
        for source_id in selected
    ))
    sources = []
    available_count = 0
    for source_id, response in zip(selected, fetched):
        if response.get("status") != "ok":
            continue
        available_count += 1
        definition = PUBLIC_SOURCES[source_id]
        snippets = _extrair_trechos(response.get("conteudo", ""), termo)
        if not snippets:
            continue
        sources.append({
            "id": source_id,
            "titulo": definition.titulo,
            "orgao": definition.orgao,
            "url": definition.url,
            "atualizado_em": _data_atualizacao(response.get("conteudo", "")),
            "trechos": snippets,
        })

    if sources:
        return {
            "status": "ok",
            "termo": termo,
            "quantidade": sum(len(source["trechos"]) for source in sources),
            "fontes": sources,
            "protocolo": "MCP",
        }
    if available_count:
        return {
            "status": "sem_dados",
            "termo": termo,
            "quantidade": 0,
            "fontes": [],
            "protocolo": "MCP",
        }
    return {
        "status": "indisponivel",
        "mensagem": "As fontes públicas oficiais estão indisponíveis no momento.",
        "fontes": [],
    }


async def consultar_fontes_publicas(
    termo: str,
    *,
    area: PublicSourceArea,
    fontes: list[PublicSourceId] | None = None,
    limite_fontes: int = 4,
) -> dict:
    """Delega pesquisa pública por A2A quando configurado; mantém fallback local."""
    if (config.A2A_PUBLIC_RESEARCH_URL and config.A2A_SHARED_TOKEN
            and len(termo) <= 300 and not re.search(
                r"@|\b\d{3}[. ]?\d{3}[. ]?\d{3}-?\d{2}\b|"
                r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
                termo,
            )):
        from app.infrastructure.a2a_public_research import get_public_research_a2a_client

        try:
            result = await get_public_research_a2a_client().consult(
                termo, area=area, fontes=fontes, limite_fontes=limite_fontes,
            )
            logger.info("Pesquisa pública concluída via A2A")
            return result
        except Exception:
            # Nenhum texto remoto é incluído no log ou no prompt após falha.
            logger.warning("Pesquisa A2A indisponível; usando consulta MCP local")
    return await _consultar_fontes_publicas_local(
        termo, area=area, fontes=fontes, limite_fontes=limite_fontes,
    )
