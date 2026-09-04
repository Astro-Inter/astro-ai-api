import base64
import binascii
import json
from threading import Lock
from typing import Any

import firebase_admin
from firebase_admin import App, auth, credentials

from app.core import config


class FirebaseConfigurationError(RuntimeError):
    """Raised when Firebase cannot be initialized from the application settings."""


_firebase_initialization_lock = Lock()


def _build_firebase_credential() -> credentials.Certificate:
    encoded_credentials = config.FIREBASE_CREDENTIALS_BASE64
    if not encoded_credentials:
        raise FirebaseConfigurationError(
            "As credenciais administrativas do Firebase nao foram configuradas."
        )

    try:
        decoded_credentials = base64.b64decode(
            encoded_credentials,
            validate=True,
        ).decode("utf-8")
        service_account = json.loads(decoded_credentials)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FirebaseConfigurationError(
            "As credenciais administrativas do Firebase sao invalidas."
        ) from error

    if not isinstance(service_account, dict):
        raise FirebaseConfigurationError(
            "As credenciais administrativas do Firebase sao invalidas."
        )

    try:
        return credentials.Certificate(service_account)
    except (KeyError, TypeError, ValueError) as error:
        raise FirebaseConfigurationError(
            "As credenciais administrativas do Firebase sao invalidas."
        ) from error


def get_firebase_app() -> App:
    try:
        return firebase_admin.get_app()
    except ValueError:
        pass

    with _firebase_initialization_lock:
        try:
            return firebase_admin.get_app()
        except ValueError:
            credential = _build_firebase_credential()
            options = (
                {"projectId": config.FIREBASE_PROJECT_ID}
                if config.FIREBASE_PROJECT_ID
                else None
            )
            return firebase_admin.initialize_app(credential, options)


def verify_firebase_id_token(id_token: str) -> dict[str, Any]:
    firebase_app = get_firebase_app()
    return auth.verify_id_token(
        id_token,
        app=firebase_app,
        check_revoked=True,
    )
