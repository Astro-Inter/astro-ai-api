"""Content moderation only: never grants access or verifies tool execution."""
import logging
import re
import time

import httpx

from app.core import config
from app.modules.chat.errors import ChatError


logger = logging.getLogger(__name__)
MODEL = "meta-llama/Llama-Guard-4-12B"
ENDPOINT = "https://api.deepinfra.com/v1/openai/chat/completions"
BLOCKED_REPLY = "Não posso atender esse pedido ou entregar essa resposta por motivos de segurança. Posso ajudar com uma orientação segura dentro do Astro."
UNAVAILABLE_REPLY = "Não consegui validar a segurança da mensagem agora. Tente novamente em instantes."


def parse_verdict(content: str) -> tuple[bool, tuple[str, ...]]:
    """Accept only the classifier's native labels, not arbitrary model prose."""
    if not isinstance(content, str):
        raise ValueError("Invalid moderation contract")
    content = content.strip()
    if content == "safe":
        return True, ()
    if content == "unsafe":
        return False, ()
    if re.fullmatch(r"unsafe\s*\n\s*S(?:[1-9]|1[0-4])(?:\s*,\s*S(?:[1-9]|1[0-4]))*", content):
        return False, tuple(dict.fromkeys(re.findall(r"S\d+", content)))
    raise ValueError("Invalid moderation contract")


async def moderate(message: str, answer: str | None = None) -> bool:
    """Send public turn only; no system prompts, UID, tool payloads or history.

    observe never replaces existing controls. enforce fails closed on outages
    and invalid/truncated output; never falls back to an unrelated chat model.
    """
    mode = config.LLAMA_GUARD_MODE
    if mode == "off":
        return True
    if mode not in {"observe", "enforce"}:
        raise ChatError(503, UNAVAILABLE_REPLY, reason="moderation_unavailable")
    started = time.monotonic()
    stage = "entrada" if answer is None else "saida"
    try:
        if not config.DEEPINFRA_API_KEY.strip():
            raise ValueError("Missing moderation credentials")
        # Refuse oversized content rather than silently leaving a tail unchecked.
        if len(message) > 32000 or (answer is not None and len(answer) > 64000):
            raise ValueError("Moderation content too large")
        messages = [{"role": "user", "content": message}]
        if answer is not None:
            messages.append({"role": "assistant", "content": answer})
        async with httpx.AsyncClient(timeout=config.LLAMA_GUARD_TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = await client.post(
                ENDPOINT,
                headers={"Authorization": f"Bearer {config.DEEPINFRA_API_KEY}"},
                json={"model": MODEL, "messages": messages, "temperature": 0, "max_tokens": 128},
            )
            response.raise_for_status()
            choice = response.json()["choices"][0]
            if not isinstance(choice, dict):
                raise ValueError("Invalid moderation envelope")
            if choice.get("finish_reason") != "stop":
                raise ValueError("Incomplete moderation output")
            safe, categories = parse_verdict(choice["message"]["content"])
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
        # Never log response bodies, credentials, user messages or raw exceptions.
        status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        logger.warning("Llama Guard indisponivel etapa=%s modo=%s status_http=%s latencia_ms=%s", stage, mode, status, round((time.monotonic() - started) * 1000))
        if mode == "observe":
            return True
        raise ChatError(503, UNAVAILABLE_REPLY, reason="moderation_unavailable") from None
    logger.info("Llama Guard etapa=%s modo=%s seguro=%s categorias=%s latencia_ms=%s", stage, mode, safe, ",".join(categories), round((time.monotonic() - started) * 1000))
    return safe if mode == "enforce" else True
