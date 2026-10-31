import asyncio
from datetime import datetime, timedelta, timezone
from functools import wraps
from uuid import uuid4

from pymongo import AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.core import config
from app.modules.chat.errors import ChatError


COLLECTION_SESSIONS = "sessoes"

def stored_message(message):
    names = {"role": "papel", "content": "conteudo"}
    return {names.get(key, key): value for key, value in message.items()}


def stored_pending(value):
    if not isinstance(value, dict):
        return value
    names = {"session_id": "sessao_id", "id_evento": "evento_id", "id_mensagem": "mensagem_id",
             "id_envia": "remetente_id", "id_recebe": "destinatario_id"}
    return {names.get(key, key): item for key, item in value.items()}


def runtime_message(value):
    if not isinstance(value, dict):
        return value
    names = {"papel": "role", "conteudo": "content"}
    return {names.get(key, key): item for key, item in value.items()}


def runtime_document(value):
    if not isinstance(value, dict):
        return value
    result = dict(value)
    if "usuario_id" in result:
        result["id_user"] = result.pop("usuario_id")
    if isinstance(result.get("mensagens"), list):
        result["mensagens"] = [runtime_message(message) for message in result["mensagens"]]
    for field in ("primeira_pergunta", "ultima_mensagem"):
        if field in result:
            result[field] = runtime_message(result[field])
    return result


LEASE_SECONDS = 180  # Maior que o timeout total de uma requisição (120s).


def utc_now():
    return datetime.now(timezone.utc)


def mongo_errors(method):
    @wraps(method)
    async def wrapped(*args, **kwargs):
        try:
            return await method(*args, **kwargs)
        except PyMongoError:
            raise ChatError(503, "Historico de conversas indisponivel.") from None
    return wrapped


class MongoSessions:
    """Um documento por sessão. Todas as operações exigem o UID autenticado."""

    def __init__(self):
        self.client = None
        self.collection = None
        self.ready = False
        self.init_lock = asyncio.Lock()

    async def connect(self):
        if self.ready:
            return self.collection
        async with self.init_lock:
            if not self.ready:
                if not config.MONGODB_URI or not config.MONGODB_DATABASE:
                    raise ChatError(503, "MongoDB nao configurado.")
                if self.client is None:
                    self.client = AsyncMongoClient(
                        config.MONGODB_URI, tz_aware=True, serverSelectionTimeoutMS=5000,
                        connectTimeoutMS=5000, timeoutMS=10000,
                        w="majority",
                    )
                    self.collection = self.client[config.MONGODB_DATABASE][COLLECTION_SESSIONS]
                await self.collection.create_index(
                    [("usuario_id", 1), ("status", 1), ("atualizada_em", -1)],
                    name="historico_usuario",
                )
                await self.collection.create_index(
                    [("usuario_id", 1), ("atualizada_em", -1), ("_id", -1)],
                    name="sessoes_usuario_atualizacao",
                )
                await self.collection.create_index(
                    [("status", 1), ("ultima_mensagem_em", 1)],
                    name="sessoes_inatividade_resumo",
                )
                await self.collection.create_index(
                    [("status", 1), ("atualizada_em", 1)],
                    name="sessoes_inatividade_legado",
                )
                self.ready = True
        return self.collection

    @mongo_errors
    async def ensure(self, session_id: str, uid: str):
        collection = await self.connect()
        now = utc_now()
        try:
            # Filtrar também pelo dono impede reutilizar IDs de outro usuário.
            await collection.update_one({"_id": session_id, "usuario_id": uid}, {"$setOnInsert": {
                "iniciada_em": now, "atualizada_em": now, "ultima_mensagem_em": now, "resumo": "",
                "mensagens": [], "status": "ativa", "resumo_indexado": False,
                "acao_pendente": None,
            }}, upsert=True)
        except DuplicateKeyError:
            # Mesmo ID + outro UID ou corrida de criação: nunca sobrescrever dono.
            pass
        return await self.get(session_id, uid)

    @mongo_errors
    async def get(self, session_id: str, uid: str):
        collection = await self.connect()
        doc = await collection.find_one({"_id": session_id, "usuario_id": uid})
        if doc is None:
            raise ChatError(404, "Conversa nao encontrada.")
        return runtime_document(doc)

    @mongo_errors
    async def list_sessions(self, uid: str, limit: int, before: tuple[datetime, str] | None = None):
        collection = await self.connect()
        query = {"usuario_id": uid}
        if before is not None:
            updated_at, session_id = before
            query["$or"] = [
                {"atualizada_em": {"$lt": updated_at}},
                {"atualizada_em": updated_at, "_id": {"$lt": session_id}},
            ]
        valid_messages = {"$filter": {
            "input": {"$ifNull": ["$mensagens", []]}, "as": "message",
            "cond": {"$and": [
                {"$in": ["$$message.papel", ["human", "assistant"]]},
                {"$eq": [{"$type": "$$message.conteudo"}, "string"]},
            ]},
        }}
        # O índice atende filtro/ordem; a projeção evita carregar o histórico inteiro.
        cursor = await collection.aggregate([
            {"$match": query},
            {"$sort": {"atualizada_em": -1, "_id": -1}},
            {"$limit": limit + 1},
            {"$project": {
                "iniciada_em": 1, "atualizada_em": 1, "status": 1,
                "titulo": 1, "ultima_mensagem_previa": 1,
                "primeira_pergunta": {"$arrayElemAt": [{"$filter": {
                    "input": {"$ifNull": ["$mensagens", []]}, "as": "message",
                    "cond": {"$and": [
                        {"$eq": ["$$message.papel", "human"]},
                        {"$eq": [{"$type": "$$message.conteudo"}, "string"]},
                    ]},
                }}, 0]},
                "ultima_mensagem": {"$arrayElemAt": [valid_messages, -1]},
            }},
        ])
        return [runtime_document(doc) async for doc in cursor]

    @mongo_errors
    async def acquire(self, session_id: str, uid: str):
        collection = await self.connect()
        now, token = utc_now(), str(uuid4())
        doc = await collection.find_one_and_update({
            "_id": session_id, "usuario_id": uid,
            "$or": [{"reserva_ate": {"$exists": False}}, {"reserva_ate": {"$lte": now}}],
        }, {"$set": {"reserva_token": token, "reserva_ate": now + timedelta(seconds=LEASE_SECONDS)}},
            return_document=ReturnDocument.AFTER)
        if doc is None:
            await self.get(session_id, uid)
            raise ChatError(409, "Aguarde a operacao anterior desta conversa.")
        return runtime_document(doc), token

    @mongo_errors
    async def update(self, session_id: str, uid: str, token: str, fields: dict, messages=None):
        collection = await self.connect()
        now = utc_now()
        stored_fields = dict(fields)
        if "acao_pendente" in stored_fields:
            stored_fields["acao_pendente"] = stored_pending(stored_fields["acao_pendente"])
        update = {"$set": {**stored_fields, "atualizada_em": now}}
        if messages:
            # O par humano/assistente é gravado atomicamente; nunca meio turno.
            update["$push"] = {"mensagens": {"$each": [stored_message(message) for message in messages]}}
            # Só uma nova troca de mensagens reinicia as 24h de inatividade.
            # Um resumo anterior permanece salvo, mas deixa de ser memória atual.
            update["$set"].update({
                "ultima_mensagem_em": now, "resumo_indexado": False,
                "resumo_tarefa_proxima_tentativa": datetime.fromtimestamp(0, timezone.utc),
                "resumo_tarefa_tentativas": 0,
            })
        result = await collection.update_one({
            "_id": session_id, "usuario_id": uid, "reserva_token": token, "reserva_ate": {"$gt": now},
        }, update)
        if not result.matched_count:
            raise ChatError(409, "A reserva da conversa expirou. Tente novamente.")

    @mongo_errors
    async def release(self, session_id: str, uid: str, token: str):
        collection = await self.connect()
        await collection.update_one(
            {"_id": session_id, "usuario_id": uid, "reserva_token": token},
            {"$unset": {"reserva_token": "", "reserva_ate": ""}},
        )

    @mongo_errors
    async def previous(self, uid: str, exclude: str, ids: list[str] | None = None, *, automatic_unindexed=False):
        collection = await self.connect()
        query = {
            "usuario_id": uid, "_id": {"$ne": exclude}, "status": {"$in": ["ativa", "encerrada"]},
            "resumo": {"$type": "string", "$ne": ""},
            "$or": [
                # Compatibilidade com resumos manuais antigos sem versão.
                {"status": "encerrada", "resumo_mensagens": {"$exists": False}},
                {"$expr": {"$and": [
                    {"$eq": ["$resumo_mensagens", {"$size": {"$ifNull": ["$mensagens", []]}}]},
                    {"$eq": ["$resumo_ultima_mensagem_em", {
                        "$ifNull": ["$ultima_mensagem_em", "$atualizada_em"],
                    }]},
                ]}},
            ],
        }
        if ids is not None:
            query["_id"]["$in"] = ids
        if automatic_unindexed:
            query.update(resumo_origem="automatico", resumo_indexado={"$ne": True})
        # Não confiar no payload vetorial: revalidar dono e buscar a fonte no Mongo.
        cursor = collection.find(query, {
            "resumo": 1, "iniciada_em": 1, "mensagens": {"$slice": -6},
        }).sort("atualizada_em", -1).limit(3)
        return [runtime_document(doc) async for doc in cursor]

    async def close(self):
        if self.client is not None:
            await self.client.close()
