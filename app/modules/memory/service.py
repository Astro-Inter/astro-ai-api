import json

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.modules.chat.errors import ChatError, InvalidAgentResponse
from app.modules.chat.prompts.resumo import RESUMO_PROMPT_COMPLETO


class SummaryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    resumo: str = Field(min_length=1, max_length=4000)


class ConversationMemory:
    def __init__(self, repository, vectors, model):
        self.repository = repository
        self.vectors = vectors
        self.model = model

    async def search(self, uid: str, session_id: str, query: str):
        origin = "recentes_mongodb"
        if query:
            try:
                ids = await self.vectors.search(uid, session_id, query)
            except ChatError:
                # A memória persistida continua disponível se o índice estiver fora.
                docs = await self.repository.previous(uid, session_id)
                origin = "busca_semantica_indisponivel_recentes_mongodb"
            else:
                docs = await self.repository.previous(uid, session_id, ids)
                origin = "busca_semantica"
        else:
            docs = await self.repository.previous(uid, session_id)
        memories = []
        for doc in docs:
            excerpt, size = [], 0
            for message in reversed(doc.get("mensagens", [])):
                if size + len(message["content"]) > 6000:
                    break
                excerpt.insert(0, message)
                size += len(message["content"])
            memories.append({
                "session_id": doc["_id"], "iniciada_em": doc["iniciada_em"].isoformat(),
                "resumo": doc["resumo"], "trecho_final": excerpt,
            })
        return {"origem": origin, "conversas": memories}

    async def summarize(self, doc: dict, token: str):
        # Checkpoints por trecho permitem continuar após timeout sem perder progresso.
        messages = doc.get("mensagens", [])
        offset = doc.get("resumo_ate", 0)
        summary = doc.get("resumo_parcial", "")
        while offset < len(messages):
            chunk, chars = [], 0
            for message in messages[offset:]:
                if chunk and chars + len(message["content"]) > 12000:
                    break
                chunk.append(message)
                chars += len(message["content"])
            text = await self.model.complete("resumo", [
                SystemMessage(content=RESUMO_PROMPT_COMPLETO),
                HumanMessage(content=json.dumps({
                    "iniciada_em": doc["iniciada_em"].isoformat(),
                    "resumo_parcial": summary, "mensagens": chunk,
                }, ensure_ascii=False)),
            ], json_mode=True)
            try:
                summary = SummaryResult.model_validate_json(text).resumo
            except (ValidationError, ValueError):
                raise InvalidAgentResponse() from None
            offset += len(chunk)
            await self.repository.update(doc["_id"], doc["id_user"], token, {
                "resumo_parcial": summary, "resumo_ate": offset,
            })
        return summary
