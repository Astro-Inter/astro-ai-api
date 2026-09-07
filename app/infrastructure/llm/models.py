from functools import lru_cache
from typing import Protocol

from langchain_core.messages import BaseMessage
from langchain_groq import ChatGroq

from app.core import config
from app.modules.chat.errors import ChatError, InvalidAgentResponse


# Escolha de modelos fica no código, conforme a configuração do projeto.
GROQ_FAST_MODEL = "openai/gpt-oss-20b"
GROQ_SPECIALIST_MODEL = "openai/gpt-oss-120b"
MISTRAL_SPECIALIST_MODEL = "mistral-small-latest"


class AgentModel(Protocol):
    async def complete(
        self, agent: str, messages: list[BaseMessage], *, json_mode: bool = False,
    ) -> str: ...


@lru_cache(maxsize=2)
def get_model(specialist: bool):
    if specialist and config.MISTRAL_API_KEY:
        from langchain_mistralai import ChatMistralAI

        return ChatMistralAI(
            model=MISTRAL_SPECIALIST_MODEL, api_key=config.MISTRAL_API_KEY,
            temperature=0, timeout=30, max_retries=1, max_tokens=1600,
        )
    if not config.GROQ_API_KEY:
        raise ChatError(503, "Provedor de IA nao configurado.")
    return ChatGroq(
        model=GROQ_SPECIALIST_MODEL if specialist else GROQ_FAST_MODEL,
        api_key=config.GROQ_API_KEY, temperature=0,
        timeout=30, max_retries=1, max_tokens=1600,
    )


class LanguageModels:
    async def complete(
        self, agent: str, messages: list[BaseMessage], *, json_mode: bool = False,
    ) -> str:
        try:
            model = get_model(agent in {"rh", "sst", "agenda"})
            if json_mode:
                model = model.bind(response_format={"type": "json_object"})
            result = await model.ainvoke(messages, config={"run_name": agent})
        except ChatError:
            raise
        except Exception:
            # Não propagar payloads/credenciais dos SDKs para respostas ou logs.
            raise ChatError(503, "Servico de IA indisponivel. Tente novamente.") from None
        if not isinstance(result.content, str) or not result.content.strip():
            raise InvalidAgentResponse()
        if len(result.content) > 16000:
            raise InvalidAgentResponse()
        return result.content.strip()
