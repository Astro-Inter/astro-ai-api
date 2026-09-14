"""Agente A2A independente para pesquisa em publicações oficiais.

Execute com ``python -m app.a2a.public_research_server``. O servidor não recebe
identidade de usuário, consulta bancos internos nem executa ações de escrita.
"""

import hmac
import json
import logging
from collections.abc import Awaitable, Callable

import uvicorn
from a2a.helpers.proto_helpers import new_task_from_user_message, new_text_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    APIKeySecurityScheme, AgentCapabilities, AgentCard, AgentInterface,
    AgentSkill,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core import config
from app.infrastructure.a2a_public_research import validate_a2a_url
from app.modules.shared.public_sources import (
    AREA_SOURCES, PublicSourceArea, PublicSourceId,
    _consultar_fontes_publicas_local,
)


logger = logging.getLogger(__name__)
Query = Callable[..., Awaitable[dict]]


class PublicResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    termo: str = Field(min_length=3, max_length=300)
    area: PublicSourceArea
    fontes: list[PublicSourceId] | None = Field(default=None, max_length=4)
    limite_fontes: int = Field(default=4, ge=1, le=4)


class PublicResearchExecutor(AgentExecutor):
    def __init__(self, query: Query = _consultar_fontes_publicas_local):
        self.query = query

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        try:
            request = PublicResearchRequest.model_validate_json(context.get_user_input())
            if request.fontes is not None and any(
                source not in AREA_SOURCES[request.area] for source in request.fontes
            ):
                raise ValueError("Fonte fora da área autorizada")
            result = await self.query(
                request.termo, area=request.area, fontes=request.fontes,
                limite_fontes=request.limite_fontes,
            )
            await updater.add_artifact(
                parts=[new_text_part(json.dumps(result, ensure_ascii=False))],
                name="fontes_publicas",
            )
            await updater.complete()
        except (ValidationError, ValueError):
            await updater.reject(new_text_message("Solicitação de pesquisa inválida."))
        except Exception:
            logger.exception("Falha ao executar pesquisa pública via A2A")
            await updater.failed(new_text_message("Pesquisa pública indisponível."))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.current_task is not None:
            updater = TaskUpdater(
                event_queue, context.current_task.id, context.current_task.context_id,
            )
            await updater.cancel()


class SharedTokenMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, token: str):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next):
        provided = request.headers.get("X-Astro-A2A-Key", "")
        if not hmac.compare_digest(provided, self.token):
            return Response(status_code=401)
        try:
            length = int(request.headers.get("content-length", "0") or 0)
        except ValueError:
            return Response(status_code=400)
        if length > 8192:
            return Response(status_code=413)
        return await call_next(request)


def create_app(
    *, token: str | None = None, base_url: str | None = None,
    query: Query = _consultar_fontes_publicas_local,
) -> Starlette:
    token = config.A2A_SHARED_TOKEN if token is None else token
    base_url = config.A2A_PUBLIC_RESEARCH_URL if base_url is None else base_url
    if not token or len(token) < 32:
        raise ValueError("A2A_SHARED_TOKEN deve conter pelo menos 32 caracteres.")
    base_url = validate_a2a_url(base_url)

    card = AgentCard(
        name="Astro Public Sources Agent",
        description="Pesquisa somente leitura em publicações oficiais de SST e legislação.",
        version="1.0.0",
        supported_interfaces=[AgentInterface(
            url=base_url + "/", protocol_binding="JSONRPC", protocol_version="1.0",
        )],
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[AgentSkill(
            id="official_public_sources", name="Consultar fontes públicas oficiais",
            description="Recupera trechos e URLs oficiais do MTE, Fundacentro e Anvisa.",
            tags=["sst", "faq", "legislação"],
            examples=["Busque uma cartilha da Fundacentro sobre riscos psicossociais"],
        )],
    )
    card.security_schemes["astro_shared_key"].api_key_security_scheme.CopyFrom(
        APIKeySecurityScheme(location="header", name="X-Astro-A2A-Key"),
    )
    card.security_requirements.add().schemes["astro_shared_key"].list.extend([])
    handler = DefaultRequestHandler(
        agent_executor=PublicResearchExecutor(query),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    return Starlette(
        routes=[
            *create_agent_card_routes(card),
            *create_jsonrpc_routes(handler, "/"),
        ],
        middleware=[Middleware(SharedTokenMiddleware, token=token)],
    )


def main() -> None:
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=8090)


if __name__ == "__main__":
    main()
