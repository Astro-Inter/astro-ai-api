import asyncio
import logging
import re

import httpx


logger = logging.getLogger(__name__)
FIREBASE_SIGN_IN_URL = (
    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
)
TRANSIENT_FIREBASE_STATUS = {500, 502, 503, 504}


class InvalidCredentialsError(Exception):
    pass


class LoginUnavailableError(Exception):
    pass


async def sign_in_with_password(email: str, password: str, api_key: str) -> dict:
    if not api_key:
        raise LoginUnavailableError()

    response = None
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        for attempt in range(2):
            try:
                response = await client.post(
                    FIREBASE_SIGN_IN_URL,
                    # Firebase accepts its API key in this header, keeping it out of URLs/logs.
                    headers={"X-Goog-Api-Key": api_key},
                    json={"email": email, "password": password, "returnSecureToken": True},
                )
            except httpx.RequestError as error:
                logger.warning(
                    "Falha de transporte no Firebase Authentication tipo=%s tentativa=%s",
                    type(error).__name__, attempt + 1,
                )
                if attempt == 0:
                    await asyncio.sleep(0.2)
                    continue
                raise LoginUnavailableError() from None
            if response.status_code not in TRANSIENT_FIREBASE_STATUS or attempt == 1:
                break
            logger.warning(
                "Falha transitoria no Firebase Authentication status=%s tentativa=%s",
                response.status_code, attempt + 1,
            )
            await asyncio.sleep(0.2)

    if response is None:
        raise LoginUnavailableError()

    if response.status_code != 200:
        try:
            error_code = response.json()["error"]["message"].split(" : ", 1)[0]
        except (ValueError, TypeError, KeyError, AttributeError):
            error_code = ""
        if error_code in {
            "EMAIL_NOT_FOUND", "INVALID_PASSWORD", "INVALID_LOGIN_CREDENTIALS",
            "USER_DISABLED", "INVALID_EMAIL",
        }:
            raise InvalidCredentialsError()
        logger.warning(
            "Firebase Authentication recusou login status=%s codigo=%s",
            response.status_code,
            error_code if re.fullmatch(r"[A-Z0-9_]{1,80}", error_code) else "DESCONHECIDO",
        )
        raise LoginUnavailableError()

    try:
        payload = response.json()
        token = payload["idToken"]
        expiration = int(payload["expiresIn"])
        if not isinstance(token, str) or not token or expiration <= 0:
            raise ValueError()
        return {"access_token": token, "expires_in": expiration}
    except (ValueError, TypeError, KeyError, OverflowError):
        raise LoginUnavailableError() from None
