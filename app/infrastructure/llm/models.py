import logging
import time
from typing import Protocol

from langchain_core.messages import BaseMessage

from app.core import config
from app.infrastructure.llm.openrouter import request
from app.infrastructure.llm.decisions import classify_route, verify_evidence
from app.modules.chat.errors import ChatError, InvalidAgentResponse
from app.observability.chat import record_decision

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

    async def _decision(self, function, *args, signal="jev_route"):
        if not config.OPENROUTER_JEV_ENABLED:
            record_decision(signal, "disabled")
            return None
        if time.monotonic() < self._jev_retry_after:
            record_decision(signal, "circuit_open")
            return None
        try:
            return await function(*args)
        except ChatError as error:
            # Alpha/model access denial need not mean the chat API is denied.
            if error.reason in {"provider_configuration", "insufficient_credits"} and error.provider_status_code != 403:
                raise
            reason = "alpha_access_denied" if error.provider_status_code == 403 else "alpha_unavailable"
        except (KeyError, TypeError, ValueError):
            reason = "invalid_response"
        self._jev_retry_after = time.monotonic() + 60
        record_decision(signal, reason)
        logger.warning("Decisao delegada ao Luna motivo=%s", reason)
        return None

    async def verify_evidence(self, state: dict) -> bool | None:
        return await self._decision(verify_evidence, state, signal="jev_evidence")

    async def complete(
        self, agent: str, messages: list[BaseMessage], *, json_mode: bool = False,
    ) -> str:
        serialized = serialize_messages(messages)
        if agent == "roteador" and not json_mode:
            # Few-shot examples teach Luna's output format; they are not session turns.
            actual = [m for m in messages if not m.additional_kwargs.get("astro_example")]
            current = [i for i, m in enumerate(actual) if m.additional_kwargs.get("astro_request")]
            if current and current[-1] != len(actual) - 1:
                # Tool results / internal repair instructions require the text router.
                record_decision("jev_route", "text_correction")
                route = None
            else:
                route = await self._decision(classify_route, serialize_messages(actual))
            if route:
                record_decision("route_engine", "jev")
                return "ROUTE=" + route
            record_decision("route_engine", "luna")
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
