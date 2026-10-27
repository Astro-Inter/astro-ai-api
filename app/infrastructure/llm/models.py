import logging
import time
from typing import Protocol

from langchain_core.messages import BaseMessage

from app.core import config
from app.infrastructure.llm.openrouter import request
from app.infrastructure.llm.decisions import classify_route, verify_evidence
from app.modules.chat.errors import ChatError, InvalidAgentResponse

TEXT_MODEL = "openai/gpt-6-luna"
TRUNCATION_RETRY_MAX_TOKENS = 4096
logger = logging.getLogger(__name__)


class AgentModel(Protocol):
    async def complete(
        self, agent: str, messages: list[BaseMessage], *, json_mode: bool = False,
    ) -> str: ...


def serialize_messages(messages: list[BaseMessage]) -> list[dict]:
    roles = {"system": "system", "human": "user", "ai": "assistant"}
    if any(m.type not in roles or not isinstance(m.content, str) for m in messages):
        raise InvalidAgentResponse()
    return [{"role": roles[m.type], "content": m.content} for m in messages]


class LanguageModels:
    def __init__(self):
        self._jev_retry_after = 0.0

    async def _decision(self, function, *args):
        if not config.OPENROUTER_JEV_ENABLED or time.monotonic() < self._jev_retry_after:
            return None
        try:
            return await function(*args)
        except ChatError as error:
            if error.reason in {"provider_configuration", "insufficient_credits"}:
                raise
        except (KeyError, TypeError, ValueError):
            pass
        self._jev_retry_after = time.monotonic() + 60
        logger.warning("Decisao delegada ao Luna motivo=alpha_indisponivel_ou_invalida")
        return None

    async def verify_evidence(self, state: dict) -> bool | None:
        return await self._decision(verify_evidence, state)

    async def complete(
        self, agent: str, messages: list[BaseMessage], *, json_mode: bool = False,
    ) -> str:
        serialized = serialize_messages(messages)
        if agent == "roteador" and not json_mode:
            route = await self._decision(classify_route, serialized)
            if route:
                return "ROUTE=" + route
        payload = {
            "model": TEXT_MODEL, "messages": serialized, "max_tokens": 1600,
            # O catálogo de Luna não anuncia temperature; none evita raciocínio oculto.
            "reasoning": {"effort": "none"},
            "provider": {"require_parameters": True, "data_collection": "deny"},
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        for attempt in range(2):
            data = await request("/v1/chat/completions", payload, stage=agent)
            try:
                choice = data["choices"][0]
                message = choice["message"]
                if choice.get("finish_reason") == "length" and not attempt:
                    payload["max_tokens"] = TRUNCATION_RETRY_MAX_TOKENS
                    logger.warning("Resposta truncada agente=%s; repetindo uma vez", agent)
                    continue
                content = message.get("content")
                if (choice.get("finish_reason") != "stop" or message.get("tool_calls")
                        or not isinstance(content, str) or not content.strip() or len(content) > 16000):
                    raise ValueError()
                return content.strip()
            except (KeyError, IndexError, TypeError, ValueError, AttributeError):
                raise InvalidAgentResponse(agent) from None
        raise InvalidAgentResponse(agent)
