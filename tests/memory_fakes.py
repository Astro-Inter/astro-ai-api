from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

from app.infrastructure.database.sessions import LEASE_SECONDS, utc_now
from app.modules.chat.errors import ChatError


class FakeSessions:
    def __init__(self):
        self.docs = {}

    async def ensure(self, sid, uid):
        if sid not in self.docs:
            self.docs[sid] = {"_id": sid, "id_user": uid, "mensagens": [], "resumo": "",
                "iniciada_em": utc_now(), "atualizada_em": utc_now(), "status": "ativa",
                "resumo_indexado": False, "acao_pendente": None}
        return await self.get(sid, uid)

    async def get(self, sid, uid):
        doc = self.docs.get(sid)
        if doc is None or doc["id_user"] != uid:
            raise ChatError(404, "Conversa nao encontrada.")
        return deepcopy(doc)

    async def list_sessions(self, uid, limit, before=None):
        docs = [deepcopy(doc) for doc in self.docs.values() if doc["id_user"] == uid]
        if before is not None:
            docs = [doc for doc in docs if (doc["atualizada_em"], doc["_id"]) < before]
        return sorted(docs, key=lambda doc: (doc["atualizada_em"], doc["_id"]), reverse=True)[:limit + 1]

    async def acquire(self, sid, uid):
        await self.get(sid, uid)
        doc = self.docs[sid]
        if doc.get("lock_ate", utc_now()) > utc_now():
            raise ChatError(409, "Conversa ocupada.")
        token = str(uuid4())
        doc.update(lock_token=token, lock_ate=utc_now() + timedelta(seconds=LEASE_SECONDS))
        return deepcopy(doc), token

    async def update(self, sid, uid, token, fields, messages=None):
        await self.get(sid, uid)
        doc = self.docs[sid]
        if doc.get("lock_token") != token or doc["lock_ate"] <= utc_now():
            raise ChatError(409, "Reserva expirada.")
        doc.update(deepcopy(fields), atualizada_em=utc_now())
        if messages:
            doc["mensagens"].extend(deepcopy(messages))
            doc.update(ultima_mensagem_em=doc["atualizada_em"], resumo_indexado=False,
                       resumo_job_proxima_tentativa=utc_now(), resumo_job_tentativas=0)

    async def release(self, sid, uid, token):
        await self.get(sid, uid)
        doc = self.docs[sid]
        if doc.get("lock_token") == token:
            doc.pop("lock_token", None)
            doc.pop("lock_ate", None)

    async def previous(self, uid, exclude, ids=None, *, automatic_unindexed=False):
        def current(doc):
            if doc["status"] == "encerrada" and "resumo_mensagens" not in doc:
                return True
            return (doc.get("resumo_mensagens") == len(doc["mensagens"])
                    and doc.get("resumo_ultima_mensagem_em") == doc.get("ultima_mensagem_em", doc["atualizada_em"]))
        docs = [deepcopy(doc) for doc in self.docs.values() if doc["id_user"] == uid
                and doc["_id"] != exclude and doc["status"] in {"ativa", "encerrada"}
                and doc.get("resumo") and current(doc) and (ids is None or doc["_id"] in ids)
                and (not automatic_unindexed or (doc.get("resumo_origem") == "automatico"
                                                 and not doc.get("resumo_indexado")))]
        return sorted(docs, key=lambda doc: doc["atualizada_em"], reverse=True)[:3]

    async def close(self):
        pass


class FakeVectors:
    def __init__(self):
        self.points = {}
        self.calls = []
        self.failure = False

    async def upsert(self, doc):
        self.calls.append(("upsert", doc["_id"]))
        if self.failure:
            raise ChatError(503, "Qdrant indisponivel.")
        self.points[doc["_id"]] = deepcopy(doc)

    async def search(self, uid, exclude, query):
        self.calls.append(("search", uid, exclude, query))
        if self.failure:
            raise ChatError(503, "Qdrant indisponivel.")
        return [sid for sid, doc in self.points.items() if doc["id_user"] == uid and sid != exclude][:3]

    async def close(self):
        pass


class FakeFaqVectors:
    def __init__(self):
        self.calls = []
        self.results = [{
            "conteudo": "O objetivo do Astro e centralizar orientacoes internas.",
            "fonte": "normas.pdf",
            "pagina": 1,
            "relevancia": 0.91,
        }]
        self.failure = False

    async def search(self, query):
        self.calls.append(query)
        if self.failure:
            raise ChatError(503, "Consulta as normas indisponivel.")
        return deepcopy(self.results)

    async def close(self):
        pass


class FakeAccessRoles:
    def __init__(self, role="COLABORADOR"):
        self.role = role
        self.calls = []

    async def get_role(self, uid):
        self.calls.append(uid)
        return self.role
