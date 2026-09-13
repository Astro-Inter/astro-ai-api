import logging
import time
from functools import lru_cache
from typing import Protocol

from langchain_core.messages import BaseMessage
from langchain_groq import ChatGroq

from app.core import config
from app.modules.chat.errors import ChatError, InvalidAgentResponse


# Escolha de modelos fica no código, conforme a configuração do projeto.
GROQ_FAST_MODEL = "openai/gpt-oss-20b"
MISTRAL_SPECIALIST_MODEL = "mistral-small-latest"
logger = logging.getLogger(__name__)
MISTRAL_COOLDOWN_SECONDS = 300


def _http_status(error: Exception):
    response = getattr(error, "response", None)
    return getattr(error, "status_code", None) or getattr(response, "status_code", None)


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
            temperature=0, timeout=30, max_retries=2, max_tokens=1600,
        )
    if not config.GROQ_API_KEY:
        raise ChatError(503, "Provedor de IA nao configurado.")
    return ChatGroq(
        model=GROQ_FAST_MODEL,
        api_key=config.GROQ_API_KEY, temperature=0,
        timeout=30, max_retries=3, max_tokens=1600,
    )


class LanguageModels:
    def __init__(self):
        self._mistral_retry_after = 0.0

    @staticmethod
    async def _invoke(model, agent: str, messages: list[BaseMessage], json_mode: bool) -> str:
        if json_mode:
            model = model.bind(response_format={"type": "json_object"})
        result = await model.ainvoke(messages, config={"run_name": agent})
        if not isinstance(result.content, str) or not result.content.strip():
            raise InvalidAgentResponse()
        if len(result.content) > 16000:
            raise InvalidAgentResponse()
        return result.content.strip()

    async def _invoke_groq(
        self, model, agent: str, messages: list[BaseMessage], json_mode: bool,
    ) -> str:
        try:
            return await self._invoke(model, agent, messages, json_mode)
        except Exception as error:
            if not json_mode or _http_status(error) != 400:
                raise
            # O Groq pode rejeitar no servidor uma geração em response_format
            # antes de devolver o texto. Repetimos sem essa restrição e deixamos
            # o schema Pydantic do agente validar o JSON localmente.
            logger.warning(
                "Modo JSON recusado pelo agente=%s provedor=groq status=400; "
                "repetindo com validacao local",
                agent,
            )
            return await self._invoke(model, agent, messages, False)

    async def complete(
        self, agent: str, messages: list[BaseMessage], *, json_mode: bool = False,
    ) -> str:
        specialist = agent in {"rh", "sst", "agenda", "eventos"}
        mistral_available = (
            specialist
            and bool(config.MISTRAL_API_KEY)
            and time.monotonic() >= self._mistral_retry_after
        )
        try:
            if specialist and not mistral_available:
                if not config.GROQ_API_KEY:
                    raise ChatError(503, "Provedor de IA nao configurado.")
                primary_model = ChatGroq(
                    model=GROQ_FAST_MODEL,
                    api_key=config.GROQ_API_KEY,
                    temperature=0,
                    timeout=30,
                    max_retries=3,
                    max_tokens=1600,
                )
                return await self._invoke_groq(
                    primary_model, agent, messages, json_mode,
                )
            primary_model = get_model(specialist)
            if not specialist:
                return await self._invoke_groq(primary_model, agent, messages, json_mode)
            return await self._invoke(primary_model, agent, messages, json_mode)
        except ChatError:
            raise
        except Exception as primary_error:
            # Se o especialista primário usa Mistral, repete a mesma chamada no
            # Groq. A chave Mistral continua configurada para os embeddings.
            if mistral_available and config.GROQ_API_KEY:
                self._mistral_retry_after = time.monotonic() + MISTRAL_COOLDOWN_SECONDS
                logger.warning(
                    "Falha no provedor do agente=%s provedor=mistral status=%s; "
                    "acionando fallback=groq",
                    agent, _http_status(primary_error),
                )
                try:
                    return await self._invoke_groq(
                        ChatGroq(
                            # A decisão de tools é curta e estruturada; o modelo
                            # leve reduz latência e consumo da cota no fallback.
                            model=GROQ_FAST_MODEL,
                            api_key=config.GROQ_API_KEY,
                            temperature=0,
                            timeout=30,
                            max_retries=3,
                            max_tokens=1600,
                        ),
                        agent,
                        messages,
                        json_mode,
                    )
                except Exception as fallback_error:
                    logger.warning(
                        "Falha no provedor do agente=%s provedor=groq status=%s",
                        agent, _http_status(fallback_error),
                    )
            else:
                logger.warning(
                    "Falha no provedor do agente=%s provedor=%s status=%s",
                    agent,
                    "groq" if not mistral_available else "mistral",
                    _http_status(primary_error),
                )
            # Não propagar payloads/credenciais dos SDKs para respostas ou logs.
            raise ChatError(503, "Servico de IA indisponivel. Tente novamente.") from None
