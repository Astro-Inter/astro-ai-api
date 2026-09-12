from datetime import datetime, timezone
from uuid import uuid4

import psycopg
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.core import config as app_config
from app.core.security import CurrentUser


COLLECTION_MESSAGES = "mensagens"
_mongo_client = None
_messages_collection = None


class EnviarMensagemArgs(BaseModel):
    """Dados permitidos para preparar ou confirmar o envio de uma mensagem."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    destinatario: str = Field(
        min_length=2,
        max_length=255,
        description="Nome ou e-mail corporativo informado pelo usuário.",
    )
    mensagem: str = Field(
        min_length=1,
        max_length=2000,
        description="Texto exato que será apresentado na prévia e enviado após confirmação.",
    )
    confirmar_envio: bool = Field(
        default=False,
        description="Use true somente na confirmação explícita de um rascunho já preparado.",
    )


def get_postgres_connection():
    """Abre uma conexão curta e somente leitura com o PostgreSQL."""
    return psycopg.connect(
        app_config.DATABASE_URL,
        autocommit=True,
        connect_timeout=5,
        options="-c statement_timeout=5000 -c default_transaction_read_only=on",
    )


def get_messages_collection():
    """Obtém a collection fixa de mensagens e prepara seu índice de consulta."""
    global _mongo_client, _messages_collection
    if _messages_collection is None:
        _mongo_client = MongoClient(
            app_config.MONGODB_URI,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            timeoutMS=10000,
            w="majority",
        )
        _messages_collection = _mongo_client[
            app_config.MONGODB_DATABASE
        ][COLLECTION_MESSAGES]
        _messages_collection.create_index(
            [("id_envia", 1), ("id_recebe", 1), ("data", -1)],
            name="mensagens_participantes_data",
        )
    return _messages_collection


def _usuario_do_contexto(runtime_config: RunnableConfig) -> CurrentUser | None:
    configurable = (runtime_config or {}).get("configurable", {})
    raw_user = configurable.get("usuario_atual")
    try:
        return raw_user if isinstance(raw_user, CurrentUser) else CurrentUser.model_validate(raw_user)
    except Exception:
        return None


def _filtro_ilike(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _resolver_destinatarios(firebase_uid: str, destinatario: str):
    by_email = "@" in destinatario
    recipient_filter = (
        "LOWER(BTRIM(destinatario.email)) = LOWER(BTRIM(%s))"
        if by_email
        else "destinatario.nome ILIKE %s ESCAPE '\\'"
    )
    recipient_value = destinatario if by_email else _filtro_ilike(destinatario)
    query = f"""
        WITH remetente AS (
            SELECT usuario.id_usuario,
                   unidade.workspace_id
              FROM usuario
              JOIN unidade
                ON unidade.id_unidade = usuario.unidade_id
             WHERE usuario.firebase_uid = %s
               AND usuario.status = 'ATIVO'
             LIMIT 1
        )
        SELECT remetente.id_usuario AS id_envia,
               destinatario.id_usuario AS id_recebe,
               destinatario.nome,
               destinatario.email
          FROM remetente
          JOIN usuario AS destinatario
            ON destinatario.id_usuario <> remetente.id_usuario
           AND destinatario.status = 'ATIVO'
          JOIN unidade AS unidade_destinatario
            ON unidade_destinatario.id_unidade = destinatario.unidade_id
           AND unidade_destinatario.workspace_id = remetente.workspace_id
         WHERE {recipient_filter}
         ORDER BY destinatario.nome, destinatario.email
         LIMIT 6
    """
    with get_postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, [firebase_uid, recipient_value])
            return cursor.fetchall()


def _rascunho_pendente(
    *, session_id: str, id_envia: int, id_recebe: int,
    nome: str, email: str, mensagem: str,
) -> dict:
    return {
        "tipo": "enviar_mensagem",
        "id_mensagem": str(uuid4()),
        "session_id": session_id,
        "id_envia": id_envia,
        "id_recebe": id_recebe,
        "destinatario_nome": nome,
        "destinatario_email": email,
        "mensagem": mensagem,
    }


def _confirmacao_valida(
    configurable: dict, pending: dict, *, session_id: str,
    id_envia: int, id_recebe: int, mensagem: str,
) -> bool:
    return (
        configurable.get("confirmacao_explicita") is True
        and pending.get("tipo") == "enviar_mensagem"
        and pending.get("session_id") == session_id
        and pending.get("id_envia") == id_envia
        and pending.get("id_recebe") == id_recebe
        and pending.get("mensagem") == mensagem
        and isinstance(pending.get("id_mensagem"), str)
    )


@tool("enviar_mensagem", args_schema=EnviarMensagemArgs)
def enviar_mensagem(
    destinatario: str,
    mensagem: str,
    confirmar_envio: bool = False,
    config: RunnableConfig = None,
) -> dict:
    """Prepara e, após confirmação explícita, envia mensagem no mesmo workspace.

    O destinatário é resolvido por nome ou e-mail no PostgreSQL. A ferramenta
    nunca recebe IDs de usuário e não acessa pessoas de outro workspace. Um nome
    ambíguo não produz envio. A gravação no MongoDB exige o rascunho da sessão e
    uma confirmação explícita validada pela aplicação.
    """
    user = _usuario_do_contexto(config)
    if user is None:
        return {"status": "erro", "mensagem": "Usuario nao identificado no contexto."}
    if user.role == "ADMIN":
        return {
            "status": "nao_aplicavel",
            "mensagem": "Administradores nao possuem workspace funcional para mensagens.",
        }
    if not app_config.DATABASE_URL:
        return {"status": "indisponivel", "mensagem": "Consulta de destinatario indisponivel."}

    try:
        matches = _resolver_destinatarios(user.uid, destinatario)
    except Exception:
        return {"status": "indisponivel", "mensagem": "Consulta de destinatario indisponivel."}

    if not matches:
        return {
            "status": "nao_encontrado",
            "mensagem": "Destinatario ativo nao encontrado no seu workspace.",
        }
    if len(matches) > 1:
        return {
            "status": "ambiguo",
            "mensagem": "Existe mais de um destinatario com esse nome. Informe o e-mail.",
            "destinatarios": [
                {"nome": row[2], "email": row[3]} for row in matches
            ],
        }

    id_envia, id_recebe, recipient_name, recipient_email = matches[0]
    configurable = (config or {}).get("configurable", {})
    session_id = configurable.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return {"status": "erro", "mensagem": "Sessao nao identificada no contexto."}

    if not confirmar_envio:
        pending = _rascunho_pendente(
            session_id=session_id,
            id_envia=id_envia,
            id_recebe=id_recebe,
            nome=recipient_name,
            email=recipient_email,
            mensagem=mensagem,
        )
        return {
            "status": "aguardando_confirmacao",
            "mensagem": "Confirme o destinatario e o texto antes do envio.",
            "rascunho": {
                "destinatario": {"nome": recipient_name, "email": recipient_email},
                "mensagem": mensagem,
            },
            "acao_pendente": pending,
        }

    pending = configurable.get("acao_pendente")
    if not isinstance(pending, dict) or not _confirmacao_valida(
        configurable,
        pending,
        session_id=session_id,
        id_envia=id_envia,
        id_recebe=id_recebe,
        mensagem=mensagem,
    ):
        return {
            "status": "confirmacao_invalida",
            "mensagem": "Prepare a mensagem novamente antes de confirmar o envio.",
        }
    if not app_config.MONGODB_URI or not app_config.MONGODB_DATABASE:
        return {"status": "indisponivel", "mensagem": "Envio de mensagens indisponivel."}

    sent_at = datetime.now(timezone.utc)
    document = {
        "_id": pending["id_mensagem"],
        "id_envia": id_envia,
        "id_recebe": id_recebe,
        "mensagem": mensagem,
        "data": sent_at,
    }
    try:
        collection = get_messages_collection()
        collection.insert_one(document)
        already_sent = False
    except DuplicateKeyError:
        try:
            existing = collection.find_one({
                "_id": pending["id_mensagem"],
                "id_envia": id_envia,
                "id_recebe": id_recebe,
                "mensagem": mensagem,
            })
        except PyMongoError:
            existing = None
        if existing is None:
            return {"status": "indisponivel", "mensagem": "Envio de mensagens indisponivel."}
        sent_at = existing["data"]
        already_sent = True
    except PyMongoError:
        return {"status": "indisponivel", "mensagem": "Envio de mensagens indisponivel."}

    return {
        "status": "ok",
        "id_mensagem": pending["id_mensagem"],
        "destinatario": {"nome": recipient_name, "email": recipient_email},
        "mensagem": mensagem,
        "data": sent_at.isoformat(),
        "envio_repetido": already_sent,
    }


TOOLS_ROTEADOR = [enviar_mensagem]
