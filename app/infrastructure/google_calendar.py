import asyncio
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

from app.core import config


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
GOOGLE_CALENDAR_SCOPES = (
    "https://www.googleapis.com/auth/calendar.events.owned",
)
INTEGRATIONS_COLLECTION = "integracoes_google_calendar"
OAUTH_STATES_COLLECTION = "integracoes_google_calendar_oauth_states"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GoogleCalendarError(Exception):
    """Erro público e sanitizado da integração com o Google Calendar."""


class GoogleCalendarConfigurationError(GoogleCalendarError):
    pass


class GoogleCalendarNotConnected(GoogleCalendarError):
    pass


class GoogleCalendarOAuthError(GoogleCalendarError):
    pass


class GoogleCalendarRepositoryError(GoogleCalendarError):
    pass


class TokenCipher:
    def __init__(self, key: str | None = None):
        value = key or config.GOOGLE_OAUTH_TOKEN_ENCRYPTION_KEY
        if not value:
            raise GoogleCalendarConfigurationError(
                "Criptografia da integração Google Calendar não configurada."
            )
        try:
            self._fernet = Fernet(value.encode("ascii"))
        except (ValueError, UnicodeError):
            raise GoogleCalendarConfigurationError(
                "Chave de criptografia da integração Google Calendar inválida."
            ) from None

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError):
            raise GoogleCalendarRepositoryError(
                "Não foi possível ler as credenciais do Google Calendar."
            ) from None


class MongoGoogleCalendarRepository:
    """Guarda tokens criptografados e estados OAuth de uso único por Firebase UID."""

    def __init__(self, *, client=None, cipher: TokenCipher | None = None):
        self.client = client
        self.cipher = cipher
        self.integrations = None
        self.states = None
        self.ready = False
        self._lock = asyncio.Lock()

    async def connect(self):
        if self.ready:
            return
        async with self._lock:
            if self.ready:
                return
            if not config.MONGODB_URI or not config.MONGODB_DATABASE:
                raise GoogleCalendarConfigurationError("MongoDB não configurado.")
            if self.cipher is None:
                self.cipher = TokenCipher()
            if self.client is None:
                self.client = AsyncMongoClient(
                    config.MONGODB_URI,
                    tz_aware=True,
                    serverSelectionTimeoutMS=5000,
                    connectTimeoutMS=5000,
                    timeoutMS=10000,
                    w="majority",
                )
            database = self.client[config.MONGODB_DATABASE]
            self.integrations = database[INTEGRATIONS_COLLECTION]
            self.states = database[OAUTH_STATES_COLLECTION]
            try:
                await self.integrations.create_index(
                    "firebase_uid", unique=True, name="google_calendar_por_usuario"
                )
                await self.states.create_index(
                    "expira_em", expireAfterSeconds=0, name="oauth_state_expiracao"
                )
            except PyMongoError:
                raise GoogleCalendarRepositoryError(
                    "Armazenamento da integração Google Calendar indisponível."
                ) from None
            self.ready = True

    async def save_oauth_state(self, state: str, uid: str, redirect_uri: str) -> None:
        await self.connect()
        try:
            await self.states.insert_one({
                "_id": hashlib.sha256(state.encode("utf-8")).hexdigest(),
                "firebase_uid": uid,
                "redirect_uri": redirect_uri,
                "expira_em": utc_now() + timedelta(minutes=10),
            })
        except PyMongoError:
            raise GoogleCalendarRepositoryError(
                "Não foi possível iniciar a conexão com o Google Calendar."
            ) from None

    async def consume_oauth_state(self, state: str) -> dict | None:
        await self.connect()
        try:
            document = await self.states.find_one_and_delete({
                "_id": hashlib.sha256(state.encode("utf-8")).hexdigest(),
                "expira_em": {"$gt": utc_now()},
            })
        except PyMongoError:
            raise GoogleCalendarRepositoryError(
                "Não foi possível validar a conexão com o Google Calendar."
            ) from None
        return document

    async def get(self, uid: str) -> dict | None:
        await self.connect()
        try:
            return await self.integrations.find_one({"firebase_uid": uid})
        except PyMongoError:
            raise GoogleCalendarRepositoryError(
                "Integração Google Calendar indisponível."
            ) from None

    async def save_tokens(self, uid: str, tokens: dict) -> None:
        await self.connect()
        access_token = tokens.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise GoogleCalendarOAuthError("O Google não retornou um token de acesso.")
        current = await self.get(uid)
        refresh_token = tokens.get("refresh_token")
        if not refresh_token and current:
            refresh_token_encrypted = current.get("refresh_token")
        elif isinstance(refresh_token, str) and refresh_token:
            refresh_token_encrypted = self.cipher.encrypt(refresh_token)
        else:
            refresh_token_encrypted = None
        expires_in = tokens.get("expires_in", 3600)
        try:
            expires_in = max(60, int(expires_in))
        except (TypeError, ValueError):
            expires_in = 3600
        scope = tokens.get("scope", " ".join(GOOGLE_CALENDAR_SCOPES))
        document = {
            "firebase_uid": uid,
            "access_token": self.cipher.encrypt(access_token),
            "refresh_token": refresh_token_encrypted,
            "token_type": tokens.get("token_type", "Bearer"),
            "scopes": scope.split() if isinstance(scope, str) else list(scope or []),
            "expira_em": utc_now() + timedelta(seconds=expires_in),
            "atualizada_em": utc_now(),
        }
        try:
            await self.integrations.update_one(
                {"firebase_uid": uid},
                {"$set": document, "$setOnInsert": {"conectada_em": utc_now()}},
                upsert=True,
            )
        except PyMongoError:
            raise GoogleCalendarRepositoryError(
                "Não foi possível salvar a conexão com o Google Calendar."
            ) from None

    async def delete(self, uid: str) -> None:
        await self.connect()
        try:
            await self.integrations.delete_one({"firebase_uid": uid})
        except PyMongoError:
            raise GoogleCalendarRepositoryError(
                "Não foi possível remover a conexão com o Google Calendar."
            ) from None

    async def close(self):
        if self.client is not None:
            await self.client.close()


def _require_oauth_configuration() -> tuple[str, str]:
    if not config.GOOGLE_OAUTH_CLIENT_ID or not config.GOOGLE_OAUTH_CLIENT_SECRET:
        raise GoogleCalendarConfigurationError(
            "OAuth do Google Calendar não configurado na aplicação."
        )
    return config.GOOGLE_OAUTH_CLIENT_ID, config.GOOGLE_OAUTH_CLIENT_SECRET


class GoogleCalendarOAuthService:
    def __init__(self, repository: MongoGoogleCalendarRepository | None = None):
        self.repository = repository or MongoGoogleCalendarRepository()

    async def connection_url(self, uid: str, redirect_uri: str) -> str:
        client_id, _ = _require_oauth_configuration()
        # Forçar a validação da chave antes de enviar o usuário ao Google.
        await self.repository.connect()
        state = secrets.token_urlsafe(32)
        await self.repository.save_oauth_state(state, uid, redirect_uri)
        return GOOGLE_AUTH_URL + "?" + urlencode({
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(GOOGLE_CALENDAR_SCOPES),
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state,
        })

    async def callback(self, code: str, state: str) -> dict:
        client_id, client_secret = _require_oauth_configuration()
        saved_state = await self.repository.consume_oauth_state(state)
        if saved_state is None:
            raise GoogleCalendarOAuthError("Autorização inválida ou expirada.")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(GOOGLE_TOKEN_URL, data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": saved_state["redirect_uri"],
                    "grant_type": "authorization_code",
                })
                response.raise_for_status()
                tokens = response.json()
        except (httpx.HTTPError, ValueError, KeyError):
            raise GoogleCalendarOAuthError(
                "O Google não concluiu a autorização do calendário."
            ) from None
        await self.repository.save_tokens(saved_state["firebase_uid"], tokens)
        return {"status": "conectado"}

    async def status(self, uid: str) -> dict:
        _require_oauth_configuration()
        document = await self.repository.get(uid)
        return {
            "conectado": bool(document and document.get("refresh_token")),
            "escopos": document.get("scopes", []) if document else [],
        }

    async def disconnect(self, uid: str) -> None:
        document = await self.repository.get(uid)
        if document:
            encrypted = document.get("access_token")
            if encrypted:
                try:
                    token = self.repository.cipher.decrypt(encrypted)
                    async with httpx.AsyncClient(timeout=5) as client:
                        await client.post(GOOGLE_REVOKE_URL, params={"token": token})
                except (GoogleCalendarError, httpx.HTTPError):
                    # A remoção local é obrigatória mesmo se a revogação remota falhar.
                    pass
        await self.repository.delete(uid)

    async def access_token(self, uid: str) -> str:
        client_id, client_secret = _require_oauth_configuration()
        document = await self.repository.get(uid)
        if not document or not document.get("refresh_token"):
            raise GoogleCalendarNotConnected("Google Calendar não conectado.")
        expires_at = document.get("expira_em")
        if isinstance(expires_at, datetime) and expires_at > utc_now() + timedelta(minutes=1):
            return self.repository.cipher.decrypt(document["access_token"])
        refresh_token = self.repository.cipher.decrypt(document["refresh_token"])
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(GOOGLE_TOKEN_URL, data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                })
                response.raise_for_status()
                tokens = response.json()
        except (httpx.HTTPError, ValueError):
            raise GoogleCalendarOAuthError(
                "Não foi possível renovar o acesso ao Google Calendar."
            ) from None
        tokens["refresh_token"] = refresh_token
        await self.repository.save_tokens(uid, tokens)
        return tokens["access_token"]

    async def close(self):
        await self.repository.close()


class GoogleCalendarAPI:
    """Acesso à API oficial; chamado exclusivamente pelas tools do servidor MCP."""

    def __init__(self, oauth: GoogleCalendarOAuthService | None = None):
        self.oauth = oauth or GoogleCalendarOAuthService()

    async def _request(
        self, uid: str, method: str, path: str, *, allowed_statuses: set[int] | None = None,
        **kwargs,
    ) -> httpx.Response:
        token = await self.oauth.access_token(uid)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.request(
                    method,
                    GOOGLE_CALENDAR_API + path,
                    headers={"Authorization": f"Bearer {token}"},
                    **kwargs,
                )
                if response.status_code not in (allowed_statuses or set()):
                    response.raise_for_status()
                return response
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {401, 403}:
                raise GoogleCalendarOAuthError(
                    "O Google recusou o acesso ao calendário. Reconecte sua conta."
                ) from None
            raise GoogleCalendarError("Google Calendar indisponível no momento.") from None
        except httpx.HTTPError:
            raise GoogleCalendarError("Google Calendar indisponível no momento.") from None

    async def list_events(
        self, uid: str, start: str, end: str, limit: int = 10,
    ) -> dict:
        response = await self._request(uid, "GET", "/calendars/primary/events", params={
            "timeMin": start,
            "timeMax": end,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": limit,
        })
        events = []
        for item in response.json().get("items", []):
            events.append({
                "id": item.get("id"),
                "titulo": item.get("summary", "Sem título"),
                "inicio": item.get("start", {}).get("dateTime") or item.get("start", {}).get("date"),
                "fim": item.get("end", {}).get("dateTime") or item.get("end", {}).get("date"),
                "status": item.get("status"),
                "link": item.get("htmlLink"),
            })
        return {"status": "ok" if events else "sem_dados", "eventos": events}

    async def create_event(
        self, uid: str, *, event_id: str, title: str, start: str, end: str,
        timezone_name: str, description: str | None = None,
    ) -> dict:
        body = {
            "id": event_id,
            "summary": title,
            "description": description or "Criado pelo Astro.",
            "start": {"dateTime": start, "timeZone": timezone_name},
            "end": {"dateTime": end, "timeZone": timezone_name},
        }
        try:
            response = await self._request(
                uid, "POST", "/calendars/primary/events",
                allowed_statuses={409}, params={"sendUpdates": "none"}, json=body,
            )
        except GoogleCalendarError:
            raise
        if response.status_code == 409:
            # O ID é definido pelo Astro. Um retry após perda de resposta recupera
            # o mesmo evento em vez de criar uma duplicata.
            response = await self._request(
                uid, "GET", f"/calendars/primary/events/{event_id}",
            )
        item = response.json()
        return {
            "status": "ok",
            "evento": {
                "id": item.get("id", event_id),
                "titulo": item.get("summary", title),
                "inicio": item.get("start", {}).get("dateTime", start),
                "fim": item.get("end", {}).get("dateTime", end),
                "link": item.get("htmlLink"),
            },
        }

    async def close(self):
        await self.oauth.close()
