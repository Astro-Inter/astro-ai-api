import logging
import math
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
GROQ_COOLDOWN_SECONDS = 60


def _http_status(error: Exception):
    response = getattr(error, "response", None)
    return getattr(error, "status_code", None) or getattr(response, "status_code", None)


def groq_api_keys() -> tuple[str, ...]:
    """Uma chave ou lista com pipe; ignora espaços, entradas vazias e duplicatas."""
    return tuple(dict.fromkeys(
        key.strip() for key in (config.GROQ_API_KEY or "").split("|") if key.strip()
    ))


def _new_groq(index: int = 0):
    keys = groq_api_keys()
    if not keys:
        raise ChatError(503, "Provedor de IA nao configurado.")
    return ChatGroq(
        model=GROQ_FAST_MODEL, api_key=keys[index], temperature=0,
        timeout=30, max_retries=0 if len(keys) > 1 else 3, max_tokens=1600,
    )


def _groq_cooldown(error: Exception) -> float:
    headers = getattr(getattr(error, "response", None), "headers", {}) or {}
    try:
        delay = float(headers.get("retry-after", GROQ_COOLDOWN_SECONDS))
    except (TypeError, ValueError):
        return GROQ_COOLDOWN_SECONDS
    return min(max(delay, 1), 86400) if math.isfinite(delay) else GROQ_COOLDOWN_SECONDS


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
    return _new_groq()


class LanguageModels:
    def __init__(self):
        self._mistral_retry_after = 0.0
        self._groq_index = 0
        self._groq_retry_after: dict[int, float] = {}

    @staticmethod
    async def _invoke(model, agent: str, messages: list[BaseMessage], json_mode: bool) -> str:
        if json_mode:
            model = model.bind(response_format={"type": "json_object"})
        result = await model.ainvoke(messages, config={"run_name": agent})
        content = result.content
        if isinstance(content, list) and content and all(
            isinstance(block, dict) and block.get("type") == "text"
            and isinstance(block.get("text"), str) for block in content
        ):
            content = "\n".join(block["text"] for block in content)
        if not isinstance(content, str) or not content.strip():
            raise InvalidAgentResponse()
        if len(content) > 16000:
            raise InvalidAgentResponse()
        return content.strip()

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

    async def _complete_groq(
        self, agent: str, messages: list[BaseMessage], json_mode: bool,
    ) -> str:
        keys = groq_api_keys()
        if not keys:
            raise ChatError(503, "Provedor de IA nao configurado.")
        start = self._groq_index % len(keys)
        for offset in range(len(keys)):
            index = (start + offset) % len(keys)
            if time.monotonic() < self._groq_retry_after.get(index, 0):
                continue
            try:
                # Mantém o cliente primário em cache; fallback e outras chaves
                # nunca recebem a string inteira com pipes como credencial.
                model = (
                    get_model(False) if index == 0 and agent not in {"rh", "sst", "agenda"}
                    else _new_groq(index)
                )
                response = await self._invoke_groq(model, agent, messages, json_mode)
                self._groq_index = index
                return response
            except ChatError:
                raise
            except Exception as error:
                if _http_status(error) != 429:
                    raise
                self._groq_retry_after[index] = time.monotonic() + _groq_cooldown(error)
                self._groq_index = (index + 1) % len(keys)
                logger.warning(
                    "Limite Groq agente=%s chave_posicao=%s total_chaves=%s status=429; "
                    "tentando proxima disponivel", agent, index + 1, len(keys),
                )
        # Não espera nem percorre a lista indefinidamente; novas requisições
        # podem reutilizar as chaves depois do cooldown informado pelo provedor.
        raise ChatError(
            503, "Servico de IA indisponivel. Tente novamente.", reason="rate_limited",
        )

    async def complete(
        self, agent: str, messages: list[BaseMessage], *, json_mode: bool = False,
    ) -> str:
        specialist = agent in {"rh", "sst", "agenda"}
        mistral_available = (
            specialist
            and bool(config.MISTRAL_API_KEY)
            and time.monotonic() >= self._mistral_retry_after
        )
        try:
            if specialist and not mistral_available:
                return await self._complete_groq(agent, messages, json_mode)
            if not specialist:
                return await self._complete_groq(agent, messages, json_mode)
            primary_model = get_model(specialist)
            return await self._invoke(primary_model, agent, messages, json_mode)
        except ChatError:
            raise
        except Exception as primary_error:
            # Se o especialista primário usa Mistral, repete a mesma chamada no
            # Groq. A chave Mistral continua configurada para os embeddings.
            if mistral_available and groq_api_keys():
                self._mistral_retry_after = time.monotonic() + MISTRAL_COOLDOWN_SECONDS
                logger.warning(
                    "Falha no provedor do agente=%s provedor=mistral status=%s; "
                    "acionando fallback=groq",
                    agent, _http_status(primary_error),
                )
                try:
                    return await self._complete_groq(agent, messages, json_mode)
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
