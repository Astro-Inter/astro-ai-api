from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.api.login import router as login_router
from app.core import config
from app.infrastructure.database.access import PostgresAccessRoles
from app.infrastructure.google_calendar import GoogleCalendarOAuthService
from app.modules.chat.service import ChatService


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(application):
        try:
            yield
        finally:
            try:
                await application.state.chat_service.close()
            finally:
                await application.state.google_calendar_oauth.close()

    application = FastAPI(
        title="Astro AI API",
        version="0.1.0",
        lifespan=lifespan,
    )
    if config.CORS_ALLOWED_ORIGINS:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=config.CORS_ALLOWED_ORIGINS,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )
    application.state.chat_service = ChatService()
    application.state.access_roles = PostgresAccessRoles()
    application.state.google_calendar_oauth = GoogleCalendarOAuthService()
    application.include_router(api_router)
    if config.ENABLE_DEV_LOGIN and config.APP_ENV in {"development", "test"}:
        application.include_router(login_router)
    return application


app = create_app()
