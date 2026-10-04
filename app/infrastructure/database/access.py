import asyncio
import logging
import re
import threading
from collections.abc import Callable

import psycopg

from app.core import config
from app.core.security import AccessRole


NO_ACCESS = "SEM_ACESSO"
ACCESS_ROLES = frozenset({"ADMIN", "GESTOR", "GESTOR_WORKSPACE", "COLABORADOR"})
logger = logging.getLogger(__name__)


def _connection_failure_reason(error: BaseException) -> str:
    # O libpq pode perder o SQLSTATE durante a abertura da conexão. Classifique
    # localmente, mas nunca devolva o texto, que pode conter credenciais e hosts.
    code = getattr(error, "sqlstate", None)
    message = str(error).lower()
    if code == "53300" or any(fragment in message for fragment in (
        "remaining connection slots", "too many connections", "too many clients",
    )):
        return "limite_conexoes"
    if code in {"28P01", "28000"} or "password authentication failed" in message:
        return "autenticacao_banco"
    if "could not translate host name" in message or "getaddrinfo" in message:
        return "dns"
    if "certificate" in message or "ssl" in message:
        return "tls"
    if isinstance(error, TimeoutError) or "timeout" in message:
        return "timeout"
    return "conexao_ou_consulta"


def _transient_connection_error(error: BaseException) -> bool:
    if _connection_failure_reason(error) in {"limite_conexoes", "autenticacao_banco"}:
        # Uma repetição imediata agrava a saturação ou repete uma senha inválida.
        return False
    code = getattr(error, "sqlstate", None)
    return (
        isinstance(error, (psycopg.OperationalError, psycopg.InterfaceError, OSError))
        and (code is None or code.startswith("08") or code in {"57P01", "57P02", "57P03"})
    )


class AccessLookupError(Exception):
    pass


class PostgresAccessRoles:
    """Resolve a role atual diretamente pela função autorizada do PostgreSQL."""

    def __init__(self, connect: Callable | None = None):
        self._connect = connect or psycopg.connect
        self._slots = threading.BoundedSemaphore(config.POSTGRES_AUTH_MAX_CONCURRENCY)

    def _query_role(self, firebase_uid: str):
        # O limite fica na thread: um timeout/cancelamento do chamador não libera
        # uma vaga enquanto a consulta síncrona ainda mantém a conexão aberta.
        if not self._slots.acquire(timeout=5):
            raise TimeoutError()
        try:
            return self._query_role_with_connection(firebase_uid)
        finally:
            self._slots.release()

    def _query_role_with_connection(self, firebase_uid: str):
        with self._connect(
            config.DATABASE_URL,
            autocommit=True,
            connect_timeout=5,
            options="-c statement_timeout=5000 -c default_transaction_read_only=on",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT fn_retornar_nivel_acesso(%s)",
                    (firebase_uid,),
                )
                return cursor.fetchone()

    async def get_role(self, firebase_uid: str) -> AccessRole | None:
        if not config.DATABASE_URL:
            raise AccessLookupError()
        try:
            async with asyncio.timeout(10):
                # psycopg assíncrono não suporta o ProactorEventLoop padrão do Windows.
                for attempt in range(2):
                    try:
                        row = await asyncio.to_thread(self._query_role, firebase_uid)
                        break
                    except (psycopg.Error, OSError) as error:
                        if attempt or not _transient_connection_error(error):
                            raise
                        logger.warning("Conexao de autorizacao interrompida; repetindo leitura uma vez")
                        await asyncio.sleep(0.1)
        except (psycopg.Error, OSError, TimeoutError) as error:
            # Nunca registra UID, DSN, SQL, senha nem o texto da exceção.
            code = getattr(error, "sqlstate", None)
            safe_code = code if isinstance(code, str) and re.fullmatch(r"[A-Z0-9]{5}", code) else "indisponivel"
            logger.warning(
                "Falha na consulta de autorizacao tipo=%s motivo=%s sqlstate=%s",
                type(error).__name__, _connection_failure_reason(error), safe_code,
            )
            raise AccessLookupError() from None

        if not row or not isinstance(row[0], str):
            logger.warning("Consulta de autorizacao sem perfil valido")
            raise AccessLookupError()
        role = row[0].strip().upper()
        if role == NO_ACCESS:
            return None
        if role not in ACCESS_ROLES:
            logger.warning("Consulta de autorizacao retornou perfil nao reconhecido")
            raise AccessLookupError()
        return role
