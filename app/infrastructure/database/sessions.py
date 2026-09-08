import asyncio
from datetime import datetime, timedelta, timezone
from functools import wraps
from uuid import uuid4

from pymongo import AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.core import config
from app.modules.chat.errors import ChatError


COLLECTION_SESSIONS = "sessoes"
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
                    [("id_user", 1), ("status", 1), ("atualizada_em", -1)],
                    name="historico_usuario",
                )
                self.ready = True
        return self.collection

    @mongo_errors
    async def ensure(self, session_id: str, uid: str):
        collection = await self.connect()
        now = utc_now()
        try:
            # Filtrar também pelo dono impede reutilizar IDs de outro usuário.
            await collection.update_one({"_id": session_id, "id_user": uid}, {"$setOnInsert": {
                "iniciada_em": now, "atualizada_em": now, "resumo": "",
                "mensagens": [], "status": "ativa", "resumo_indexado": False,
            }}, upsert=True)
        except DuplicateKeyError:
            # Mesmo ID + outro UID ou corrida de criação: nunca sobrescrever dono.
            pass
        return await self.get(session_id, uid)

    @mongo_errors
    async def get(self, session_id: str, uid: str):
        collection = await self.connect()
        doc = await collection.find_one({"_id": session_id, "id_user": uid})
        if doc is None:
            raise ChatError(404, "Conversa nao encontrada.")
        return doc

    @mongo_errors
    async def acquire(self, session_id: str, uid: str):
        collection = await self.connect()
        now, token = utc_now(), str(uuid4())
        doc = await collection.find_one_and_update({
            "_id": session_id, "id_user": uid,
            "$or": [{"lock_ate": {"$exists": False}}, {"lock_ate": {"$lte": now}}],
        }, {"$set": {"lock_token": token, "lock_ate": now + timedelta(seconds=LEASE_SECONDS)}},
            return_document=ReturnDocument.AFTER)
        if doc is None:
            await self.get(session_id, uid)
            raise ChatError(409, "Aguarde a operacao anterior desta conversa.")
        return doc, token

    @mongo_errors
    async def update(self, session_id: str, uid: str, token: str, fields: dict, messages=None):
        collection = await self.connect()
        now = utc_now()
        update = {"$set": {**fields, "atualizada_em": now}}
        if messages:
            # O par humano/assistente é gravado atomicamente; nunca meio turno.
            update["$push"] = {"mensagens": {"$each": messages}}
        result = await collection.update_one({
            "_id": session_id, "id_user": uid, "lock_token": token, "lock_ate": {"$gt": now},
        }, update)
        if not result.matched_count:
            raise ChatError(409, "A reserva da conversa expirou. Tente novamente.")

    @mongo_errors
    async def release(self, session_id: str, uid: str, token: str):
        collection = await self.connect()
        await collection.update_one(
            {"_id": session_id, "id_user": uid, "lock_token": token},
            {"$unset": {"lock_token": "", "lock_ate": ""}},
        )

    @mongo_errors
    async def previous(self, uid: str, exclude: str, ids: list[str] | None = None):
        collection = await self.connect()
        query = {
            "id_user": uid, "_id": {"$ne": exclude}, "status": "encerrada",
            "resumo": {"$type": "string", "$ne": ""},
        }
        if ids is not None:
            query["_id"]["$in"] = ids
        # Não confiar no payload vetorial: revalidar dono e buscar a fonte no Mongo.
        cursor = collection.find(query, {
            "resumo": 1, "iniciada_em": 1, "mensagens": {"$slice": -6},
        }).sort("atualizada_em", -1).limit(3)
        return [doc async for doc in cursor]

    async def close(self):
        if self.client is not None:
            await self.client.close()
