"""Cliente A2A restrito para o agente Astro de pesquisa pública.

O endereço é fixado pela configuração, nunca por mensagens do usuário. O cartão
remoto e as evidências retornadas são validados antes de chegar ao chat.
"""

import json
import re
from urllib.parse import urlsplit

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.helpers.proto_helpers import new_text_message
from a2a.types import Role, SendMessageRequest, TaskState

from app.core import config


AGENT_NAME = "Astro Public Sources Agent"
SKILL_ID = "official_public_sources"
MAX_RESPONSE_CHARACTERS = 20000


def validate_a2a_url(value: str) -> str:
    """Aceita HTTPS remoto ou HTTP somente em loopback, sem caminhos/redireções."""
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise ValueError("Endereço A2A inválido.")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("A2A remoto exige HTTPS.")
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("Porta A2A inválida.") from error
    return value.rstrip("/")


def _validate_card(card, base_url: str) -> None:
    if (card.name != AGENT_NAME or
            SKILL_ID not in {skill.id for skill in card.skills} or
            len(card.supported_interfaces) != 1):
        raise ValueError("Agent Card A2A inesperado.")
    interface = card.supported_interfaces[0]
    if (interface.protocol_binding != "JSONRPC" or
            interface.protocol_version != "1.0" or
            interface.url.rstrip("/") != base_url):
        raise ValueError("Endpoint A2A não corresponde ao endereço autorizado.")


def _validate_evidence(raw: str, *, area: str, termo: str) -> dict:
    from app.modules.shared.public_sources import AREA_SOURCES, PUBLIC_SOURCES

    if len(raw) > MAX_RESPONSE_CHARACTERS:
        raise ValueError("Resposta A2A acima do limite.")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("status") not in {
        "ok", "sem_dados", "indisponivel", "nao_autorizado",
    }:
        raise ValueError("Resposta A2A inválida.")
    status = payload["status"]
    if status != "ok":
        return {"status": status, "termo": termo, "quantidade": 0, "fontes": []}

    items = payload.get("fontes")
    if not isinstance(items, list) or len(items) > 4:
        raise ValueError("Fontes A2A inválidas.")
    sources = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Fonte A2A inválida.")
        source_id = item.get("id")
        if (source_id not in AREA_SOURCES[area] or source_id in seen
                or item.get("url") != PUBLIC_SOURCES[source_id].url):
            raise ValueError("Fonte A2A não autorizada.")
        excerpts = item.get("trechos")
        if (not isinstance(excerpts, list) or len(excerpts) > 1 or
                any(not isinstance(part, str) or not part.strip() or len(part) > 600
                    for part in excerpts)):
            raise ValueError("Trechos A2A inválidos.")
        updated = item.get("atualizado_em")
        if updated is not None and (
            not isinstance(updated, str) or not re.fullmatch(r"\d{2}/\d{2}/\d{4}", updated)
        ):
            raise ValueError("Data A2A inválida.")
        seen.add(source_id)
        definition = PUBLIC_SOURCES[source_id]
        sources.append({
            "id": source_id, "titulo": definition.titulo, "orgao": definition.orgao,
            "url": definition.url, "atualizado_em": updated, "trechos": excerpts,
        })
    return {
        "status": "ok" if sources else "sem_dados",
        "termo": termo,
        "quantidade": sum(len(item["trechos"]) for item in sources),
        "fontes": sources,
        "protocolo": "A2A",
    }


class PublicResearchA2AClient:
    async def consult(
        self, termo: str, *, area: str, fontes: list[str] | None,
        limite_fontes: int, http_client: httpx.AsyncClient | None = None,
    ) -> dict:
        base_url = validate_a2a_url(config.A2A_PUBLIC_RESEARCH_URL)
        if not config.A2A_SHARED_TOKEN:
            raise ValueError("Chave A2A ausente.")
        own_client = http_client is None
        http_client = http_client or httpx.AsyncClient(
            headers={"X-Astro-A2A-Key": config.A2A_SHARED_TOKEN},
            timeout=config.A2A_PUBLIC_RESEARCH_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
        )
        try:
            card = await A2ACardResolver(http_client, base_url).get_agent_card()
            _validate_card(card, base_url)
            factory = ClientFactory(ClientConfig(
                streaming=False, httpx_client=http_client,
                supported_protocol_bindings=["JSONRPC"],
            ))
            agent = factory.create(card)
            request = SendMessageRequest(message=new_text_message(
                json.dumps({
                    "termo": termo, "area": area, "fontes": fontes,
                    "limite_fontes": limite_fontes,
                }, ensure_ascii=False),
                role=Role.ROLE_USER,
            ))
            response_task = None
            async for response in agent.send_message(request):
                if response.HasField("task"):
                    response_task = response.task
            if response_task is None or response_task.status.state != TaskState.TASK_STATE_COMPLETED:
                raise ValueError("Tarefa A2A não concluída.")
            artifacts = [
                part.text for artifact in response_task.artifacts
                for part in artifact.parts if part.HasField("text")
            ]
            if len(artifacts) != 1:
                raise ValueError("Artefato A2A inválido.")
            return _validate_evidence(artifacts[0], area=area, termo=termo)
        finally:
            if own_client:
                await http_client.aclose()


_default_client = PublicResearchA2AClient()


def get_public_research_a2a_client() -> PublicResearchA2AClient:
    return _default_client
