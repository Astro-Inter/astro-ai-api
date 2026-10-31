"""GETs fixos à API Astro; credenciais nunca entram no estado dos agentes."""
from contextvars import ContextVar
from urllib.parse import urlsplit

import httpx
from app.core import config

request_token: ContextVar[str | None] = ContextVar("astro_query_token", default=None)


async def read_api(path: str, params: dict | None = None):
    token = request_token.get()
    base = config.ASTRO_API_BASE_URL.rstrip("/")
    parsed = urlsplit(base)
    if not token or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return {"status": "indisponivel", "mensagem": "Consulta à API Astro indisponível."}
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=False, trust_env=False) as client:
            # One bounded read: no retry fan-out, redirects or model-supplied URLs.
            async with client.stream("GET", base + path, params=params, headers={"Authorization": "Bearer " + token}) as response:
                if response.status_code in {401, 403}:
                    return {"status": "nao_autorizado", "mensagem": "A API não autorizou esta consulta."}
                if response.status_code in {400, 422}:
                    return {"status": "esclarecer", "mensagem": "A API recusou os filtros ou a referência. Confira o registro selecionado."}
                if response.status_code == 404:
                    return {"status": "sem_dados", "mensagem": "Registro não encontrado no seu escopo."}
                if response.status_code != 200:
                    return {"status": "indisponivel", "mensagem": "Consulta temporariamente indisponível."}
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > 2_000_000:
                        return {"status": "indisponivel", "mensagem": "Resultado excede o limite seguro de consulta."}
        import json
        body = json.loads(payload)
        if not isinstance(body, dict) or body.get("success") is not True or "data" not in body:
            return {"status": "indisponivel", "mensagem": "Resposta inválida da API Astro."}
        return {"status": "ok", "data": body["data"]}
    except (httpx.HTTPError, ValueError):
        return {"status": "indisponivel", "mensagem": "Consulta temporariamente indisponível."}
