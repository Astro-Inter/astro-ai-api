from app.core import config
from app.infrastructure.firebase_auth import sign_in_with_password
from app.modules.auth.schemas import LoginRequest, LoginResponse


async def login(credentials: LoginRequest) -> LoginResponse:
    result = await sign_in_with_password(
        credentials.email,
        credentials.password.get_secret_value(),
        config.FIREBASE_WEB_API_KEY or "",
    )
    return LoginResponse(**result)
