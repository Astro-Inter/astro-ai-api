from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth
from firebase_admin.exceptions import FirebaseError

from app.core.security import CurrentUser
from app.infrastructure.database.access import AccessLookupError
from app.infrastructure.firebase import (
    FirebaseConfigurationError,
    verify_firebase_id_token,
)


firebase_bearer = HTTPBearer(auto_error=False)


def _authentication_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciais de autenticacao invalidas.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _uid_from_firebase_claims(claims: dict[str, Any]) -> str:
    uid = claims.get("uid")
    if not isinstance(uid, str) or not uid:
        raise _authentication_error()

    return uid


async def get_current_user(
    request: Request,
    bearer_credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(firebase_bearer),
    ],
) -> CurrentUser:
    if (
        bearer_credentials is None
        or bearer_credentials.scheme.lower() != "bearer"
        or not bearer_credentials.credentials
    ):
        raise _authentication_error()

    try:
        claims = verify_firebase_id_token(bearer_credentials.credentials)
    except FirebaseConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servico de autenticacao indisponivel.",
        ) from error
    except (
        auth.ExpiredIdTokenError,
        auth.InvalidIdTokenError,
        auth.RevokedIdTokenError,
        auth.UserDisabledError,
    ) as error:
        raise _authentication_error() from error
    except FirebaseError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servico de autenticacao indisponivel.",
        ) from error

    uid = _uid_from_firebase_claims(claims)
    try:
        role = await request.app.state.access_roles.get_role(uid)
    except AccessLookupError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servico de autorizacao indisponivel.",
        ) from None
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuario sem acesso ao Astro.",
        )
    return CurrentUser(uid=uid, role=role)


CurrentUserDependency = Annotated[CurrentUser, Depends(get_current_user)]
