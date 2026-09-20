import os
import re

from fastapi import APIRouter, Response


router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/version")
def deployed_version(response: Response) -> dict[str, str]:
    """Permite conferir se o Render executa o commit esperado, sem expor configuração."""
    response.headers["Cache-Control"] = "no-store"
    revision = os.getenv("RENDER_GIT_COMMIT", "")
    return {"commit": revision if re.fullmatch(r"[0-9a-fA-F]{7,64}", revision) else "unknown"}
