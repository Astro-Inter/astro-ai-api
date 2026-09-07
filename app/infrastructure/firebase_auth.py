import httpx


class InvalidCredentialsError(Exception):
    pass


class LoginUnavailableError(Exception):
    pass


async def sign_in_with_password(email: str, password: str, api_key: str) -> dict:
    if not api_key:
        raise LoginUnavailableError()

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.post(
                "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
                # Firebase accepts its API key in this header, keeping it out of URLs/logs.
                headers={"X-Goog-Api-Key": api_key},
                json={"email": email, "password": password, "returnSecureToken": True},
            )
    except httpx.RequestError:
        raise LoginUnavailableError() from None

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
