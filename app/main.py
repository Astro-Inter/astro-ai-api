from fastapi import FastAPI

from app.api.router import api_router


def create_app() -> FastAPI:
    application = FastAPI(
        title="Astro AI API",
        version="0.1.0",
    )
    application.include_router(api_router)
    return application


app = create_app()
