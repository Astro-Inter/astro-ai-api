from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel

from app.api.auth import CurrentUserDependency
from app.core import config
from app.infrastructure.google_calendar import (
    GoogleCalendarConfigurationError,
    GoogleCalendarError,
    GoogleCalendarOAuthError,
)


router = APIRouter(prefix="/integracoes/google-calendar", tags=["integracoes"])


class GoogleCalendarStatusResponse(BaseModel):
    conectado: bool
    escopos: list[str]


class GoogleCalendarConnectResponse(BaseModel):
    authorization_url: str


class GoogleCalendarCallbackResponse(BaseModel):
    status: str
    mensagem: str


def _service_error(error: GoogleCalendarError) -> HTTPException:
    if isinstance(error, GoogleCalendarConfigurationError):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error))
    if isinstance(error, GoogleCalendarOAuthError):
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(error))
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Integração Google Calendar indisponível.",
    )


@router.get("/status", response_model=GoogleCalendarStatusResponse)
async def google_calendar_status(
    user: CurrentUserDependency, request: Request, response: Response,
) -> GoogleCalendarStatusResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await request.app.state.google_calendar_oauth.status(user.uid)
    except GoogleCalendarError as error:
        raise _service_error(error) from None
    return GoogleCalendarStatusResponse(**result)


@router.get("/conectar", response_model=GoogleCalendarConnectResponse)
async def google_calendar_connect(
    user: CurrentUserDependency, request: Request, response: Response,
) -> GoogleCalendarConnectResponse:
    response.headers["Cache-Control"] = "no-store"
    redirect_uri = config.GOOGLE_OAUTH_REDIRECT_URI or str(
        request.url_for("google_calendar_callback")
    )
    try:
        url = await request.app.state.google_calendar_oauth.connection_url(
            user.uid, redirect_uri
        )
    except GoogleCalendarError as error:
        raise _service_error(error) from None
    return GoogleCalendarConnectResponse(authorization_url=url)


@router.get("/callback", response_model=GoogleCalendarCallbackResponse)
async def google_calendar_callback(
    request: Request,
    code: Annotated[str | None, Query(min_length=1)] = None,
    state_value: Annotated[str | None, Query(alias="state", min_length=1)] = None,
    error: Annotated[str | None, Query()] = None,
) -> GoogleCalendarCallbackResponse:
    if error:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="A autorização do Google Calendar foi cancelada ou recusada.",
        )
    if not code or not state_value:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Retorno OAuth incompleto.",
        )
    try:
        await request.app.state.google_calendar_oauth.callback(code, state_value)
    except GoogleCalendarError as service_error:
        raise _service_error(service_error) from None
    return GoogleCalendarCallbackResponse(
        status="conectado",
        mensagem="Google Calendar conectado. Você já pode voltar ao chat.",
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def google_calendar_disconnect(
    user: CurrentUserDependency, request: Request,
) -> Response:
    try:
        await request.app.state.google_calendar_oauth.disconnect(user.uid)
    except GoogleCalendarError as error:
        raise _service_error(error) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
