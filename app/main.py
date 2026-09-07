from fastapi import FastAPI

from app.api.router import api_router
from app.api.login import router as login_router
from app.core import config


def create_app() -> FastAPI:
    application = FastAPI(
        title="Astro AI API",
        version="0.1.0",
    )
    application.include_router(api_router)
    if config.ENABLE_DEV_LOGIN and config.APP_ENV in {"development", "test"}:
        application.include_router(login_router)
    return application


app = create_app()
