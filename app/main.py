from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.api.login import router as login_router
from app.core import config
from app.infrastructure.database.access import PostgresAccessRoles
from app.modules.chat.service import ChatService


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(application):
        try:
            yield
        finally:
            await application.state.chat_service.close()

    application = FastAPI(
        title="Astro AI API",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.chat_service = ChatService()
    application.state.access_roles = PostgresAccessRoles()
    application.include_router(api_router)
    if config.ENABLE_DEV_LOGIN and config.APP_ENV in {"development", "test"}:
        application.include_router(login_router)
    return application


app = create_app()
