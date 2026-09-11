from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.infrastructure.firebase_auth import InvalidCredentialsError, LoginUnavailableError
from app.modules.auth.schemas import LoginRequest, LoginResponse
from app.modules.auth.service import login


class LoginRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                # Validation errors can contain the submitted password or request body.
                return JSONResponse(
                    status_code=422,
                    content={"detail": "Informe email e senha validos."},
                    headers={"Cache-Control": "no-store"},
                )

        return safe_handler


router = APIRouter(prefix="/auth", tags=["authentication"], route_class=LoginRoute)


@router.post("/login", response_model=LoginResponse)
async def login_user(credentials: LoginRequest, response: Response) -> LoginResponse:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    try:
        return await login(credentials)
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=401,
            detail="Email ou senha invalidos.",
            headers={"Cache-Control": "no-store"},
        ) from None
    except LoginUnavailableError:
        raise HTTPException(
            status_code=503,
            detail="Servico de autenticacao indisponivel.",
            headers={"Cache-Control": "no-store"},
        ) from None
