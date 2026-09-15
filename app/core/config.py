import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[2]

load_dotenv(BASE_DIR / ".env", override=False)


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _cors_origins_env(name: str) -> list[str]:
    origins: list[str] = []
    for raw_origin in os.getenv(name, "").split(","):
        origin = raw_origin.strip().rstrip("/")
        if not origin or origin in {"*", "null"}:
            continue
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            continue
        if origin not in origins:
            origins.append(origin)
    return origins


APP_ENV = os.getenv("APP_ENV", "development")

FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")
FIREBASE_CREDENTIALS_BASE64 = os.getenv("FIREBASE_CREDENTIALS_BASE64")
FIREBASE_WEB_API_KEY = os.getenv("FIREBASE_WEB_API_KEY")
ENABLE_DEV_LOGIN = os.getenv("ENABLE_DEV_LOGIN", "false").lower() == "true"
CORS_ALLOWED_ORIGINS = _cors_origins_env("CORS_ALLOWED_ORIGINS")

DATABASE_URL = os.getenv("DATABASE_URL")

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE")

REDIS_URL = os.getenv("REDIS_URL")

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")

R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_ENDPOINT = os.getenv("R2_ENDPOINT")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")

GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
GOOGLE_OAUTH_REDIRECT_URI = os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
GOOGLE_OAUTH_TOKEN_ENCRYPTION_KEY = os.getenv("GOOGLE_OAUTH_TOKEN_ENCRYPTION_KEY")

FETCH_MCP_ALLOWED_DOMAINS = os.getenv("FETCH_MCP_ALLOWED_DOMAINS", "gov.br")
FETCH_MCP_CACHE_TTL_SECONDS = _bounded_int_env(
    "FETCH_MCP_CACHE_TTL_SECONDS", 900, 0, 86400,
)

# Agente A2A de pesquisa pública, executado como serviço independente.
A2A_PUBLIC_RESEARCH_URL = os.getenv("A2A_PUBLIC_RESEARCH_URL", "").strip()
A2A_SHARED_TOKEN = os.getenv("A2A_SHARED_TOKEN", "")
A2A_PUBLIC_RESEARCH_TIMEOUT_SECONDS = _bounded_int_env(
    "A2A_PUBLIC_RESEARCH_TIMEOUT_SECONDS", 15, 2, 30,
)

LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LANGSMITH_ENDPOINT = os.getenv(
    "LANGSMITH_ENDPOINT",
    "https://api.smith.langchain.com",
)
LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY")
LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "astro-ai-api")
LANGSMITH_WORKSPACE_ID = os.getenv("LANGSMITH_WORKSPACE_ID")


OBRIGATORIAS = {
    "DATABASE_URL": DATABASE_URL,
    "MONGODB_URI": MONGODB_URI,
    "MONGODB_DATABASE": MONGODB_DATABASE,
    "REDIS_URL": REDIS_URL,
    "QDRANT_URL": QDRANT_URL,
    "GROQ_API_KEY": GROQ_API_KEY,
    "MISTRAL_API_KEY": MISTRAL_API_KEY,
}


def validar_config() -> list[str]:
    problemas = [
        f"Variavel ausente no .env: {nome}"
        for nome, valor in OBRIGATORIAS.items()
        if not valor
    ]

    if A2A_PUBLIC_RESEARCH_URL and len(A2A_SHARED_TOKEN) < 32:
        problemas.append("A2A_SHARED_TOKEN deve conter pelo menos 32 caracteres.")
    if A2A_SHARED_TOKEN and not A2A_PUBLIC_RESEARCH_URL:
        problemas.append("Variavel ausente no .env: A2A_PUBLIC_RESEARCH_URL")

    if LANGSMITH_TRACING:
        if not LANGSMITH_ENDPOINT:
            problemas.append("Variavel ausente no .env: LANGSMITH_ENDPOINT")
        if not LANGSMITH_API_KEY:
            problemas.append("Variavel ausente no .env: LANGSMITH_API_KEY")
        if not LANGSMITH_PROJECT:
            problemas.append("Variavel ausente no .env: LANGSMITH_PROJECT")

    return problemas
