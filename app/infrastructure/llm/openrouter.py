"""Única saída HTTP para inferência, sem payloads nos erros ou logs."""
import asyncio
import logging
import math
import time

import httpx

from app.core import config
from app.modules.chat.errors import ChatError

logger = logging.getLogger(__name__)
BASE_URL = "https://openrouter.ai/api"
TRANSIENT = {408, 429, 500, 502, 503, 504, 524, 529}


def retry_after(headers) -> int:
    try:
        value = float(headers.get("retry-after", 1))
        return max(1, min(60, math.ceil(value))) if math.isfinite(value) else 1
    except (TypeError, ValueError):
        return 1


async def request(path: str, payload: dict, *, stage: str, attempts: int = 2) -> dict:
    if not config.OPENROUTER_API_KEY:
        raise ChatError(503, "OpenRouter nao configurado.", reason="provider_configuration")
    if path not in {"/v1/chat/completions", "/v1/embeddings", "/alpha/decisions"}:
        raise ValueError("Endpoint de inferencia invalido")
    for attempt in range(attempts):
        started = time.monotonic()
        status, delay = None, 1
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
                response = await client.post(
                    BASE_URL + path,
                    headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
                    json=payload,
                )
            status, delay = response.status_code, retry_after(response.headers)
            if response.is_success:
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError()
                if "error" not in data:
                    usage = data.get("usage") or {}
                    safe_usage = {k: v for k, v in usage.items()
                                  if k in {"prompt_tokens", "completion_tokens", "total_tokens",
                                           "input_tokens", "output_tokens", "cost"}
                                  and type(v) in {int, float} and math.isfinite(v)}
                    logger.info("OpenRouter agente=%s modelo=%s duracao_ms=%d uso=%s",
                                stage, payload["model"], (time.monotonic() - started) * 1000,
                                safe_usage)
                    return data
                code = data["error"].get("code") if isinstance(data["error"], dict) else None
                status = int(code) if str(code).isdigit() else 502
        except (httpx.TimeoutException, httpx.TransportError):
            status = 503
        except (ValueError, TypeError, AttributeError):
            raise ChatError(502, "Resposta do OpenRouter invalida.", reason="provider_response") from None
        logger.warning("Falha OpenRouter agente=%s status=%s tentativa=%d", stage, status, attempt + 1)
        if status in TRANSIENT and attempt + 1 < attempts and delay <= 2:
            await asyncio.sleep(delay)
            continue
        reason = {401: "provider_configuration", 403: "provider_configuration",
                  402: "insufficient_credits", 404: "model_unavailable",
                  429: "rate_limited"}.get(status, "provider_failure")
        raise ChatError(503, "Servico de IA indisponivel. Tente novamente em instantes.",
                        reason=reason, retry_after=delay if status == 429 else None) from None
    raise ChatError(503, "Servico de IA indisponivel.", reason="provider_failure")
