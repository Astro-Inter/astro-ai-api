import asyncio
import logging
from collections.abc import Callable

import psycopg

from app.core import config
from app.core.security import AccessRole


NO_ACCESS = "SEM_ACESSO"
ACCESS_ROLES = frozenset({"ADMIN", "GESTOR", "GESTOR_WORKSPACE", "COLABORADOR"})
logger = logging.getLogger(__name__)


def _transient_connection_error(error: BaseException) -> bool:
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

    def _query_role(self, firebase_uid: str):
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
            logger.warning("Falha na consulta de autorizacao tipo=%s", type(error).__name__)
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
