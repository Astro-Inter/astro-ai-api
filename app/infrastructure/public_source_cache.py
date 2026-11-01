"""Disposable Redis cache for public MCP source documents, never model answers."""

import asyncio
import hashlib
import json
import logging
import time
from urllib.parse import urlsplit

from app.core import config

logger = logging.getLogger(__name__)
PREFIX = "ia:cache:fonte_publica:v1:"
MAX_BYTES = 262144
_now = time.time
_monotonic = time.monotonic


def cache_key(url, max_length, start_index, raw):
    value = json.dumps(
        [url, max_length, start_index, raw], ensure_ascii=True, separators=(",", ":")
    )
    return PREFIX + hashlib.sha256(value.encode()).hexdigest()


def valid_result(result, url, max_length):
    return (
        isinstance(result, dict)
        and set(result)
        == {"status", "url", "conteudo", "truncado", "proximo_indice", "protocolo"}
        and result["status"] == "ok"
        and result["url"] == url
        and result["protocolo"] == "MCP"
        and isinstance(result["conteudo"], str)
        and 0 < len(result["conteudo"]) <= max_length
        and not result["conteudo"].startswith("<error>")
        and type(result["truncado"]) is bool
        and (
            (
                result["truncado"]
                and type(result["proximo_indice"]) is int
                and 0 <= result["proximo_indice"] <= 500000
            )
            or (not result["truncado"] and result["proximo_indice"] is None)
        )
    )


class PublicSourceCache:
    def __init__(self):
        self.retry_after = 0.0

    def enabled(self, url, ttl):
        parsed = urlsplit(url)
        return (
            config.FETCH_MCP_REDIS_CACHE_ENABLED
            and bool(config.REDIS_URL)
            and ttl > 0
            and not parsed.query
            and not parsed.fragment
            and _monotonic() >= self.retry_after
        )

    async def _command(self, operation, *args, **kwargs):
        try:
            from redis.asyncio import Redis

            async with asyncio.timeout(0.3):
                async with Redis.from_url(
                    config.REDIS_URL,
                    socket_connect_timeout=0.1,
                    socket_timeout=0.1,
                    retry_on_timeout=False,
                    decode_responses=False,
                ) as client:
                    return await getattr(client, operation)(*args, **kwargs)
        except Exception:
            self.retry_after = _monotonic() + 30
            logger.info("public_source_cache status=unavailable")
            return None

    async def get(self, url, max_length, start_index, raw, ttl):
        if not self.enabled(url, ttl):
            return None
        value = await self._command("get", cache_key(url, max_length, start_index, raw))
        if value is None:
            logger.debug("public_source_cache status=miss")
            return None
        try:
            if not isinstance(value, (str, bytes)) or len(value) > MAX_BYTES:
                raise ValueError()
            envelope = json.loads(value)
            created, expires = envelope["criado_em"], envelope["expira_em"]
            now = _now()
            if (
                envelope["versao"] != 1
                or type(created) not in {int, float}
                or type(expires) not in {int, float}
                or not 0 < expires - created <= 86400
                or created > now + 1
                or not 0 < min(expires, created + ttl) - now <= ttl
                or not valid_result(envelope["resultado"], url, max_length)
            ):
                raise ValueError()
            logger.debug("public_source_cache status=hit")
            return envelope["resultado"], min(expires, created + ttl) - now
        except (KeyError, TypeError, ValueError):
            logger.info("public_source_cache status=invalid")
            return None

    async def put(self, url, max_length, start_index, raw, ttl, result):
        if not self.enabled(url, ttl) or not valid_result(result, url, max_length):
            return
        now = _now()
        value = json.dumps(
            {
                "versao": 1,
                "criado_em": now,
                "expira_em": now + ttl,
                "resultado": result,
            },
            ensure_ascii=True,
            allow_nan=False,
        )
        if len(value.encode()) > MAX_BYTES:
            return
        await self._command(
            "set", cache_key(url, max_length, start_index, raw), value, ex=ttl
        )
