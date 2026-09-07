import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[2]

load_dotenv(BASE_DIR / ".env", override=False)


APP_ENV = os.getenv("APP_ENV", "development")

FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")
FIREBASE_CREDENTIALS_BASE64 = os.getenv("FIREBASE_CREDENTIALS_BASE64")
FIREBASE_WEB_API_KEY = os.getenv("FIREBASE_WEB_API_KEY")
ENABLE_DEV_LOGIN = os.getenv("ENABLE_DEV_LOGIN", "false").lower() == "true"

DATABASE_URL = os.getenv("DATABASE_URL")

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE")

REDIS_URL = os.getenv("REDIS_URL")

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")

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

    if LANGSMITH_TRACING:
        if not LANGSMITH_ENDPOINT:
            problemas.append("Variavel ausente no .env: LANGSMITH_ENDPOINT")
        if not LANGSMITH_API_KEY:
            problemas.append("Variavel ausente no .env: LANGSMITH_API_KEY")
        if not LANGSMITH_PROJECT:
            problemas.append("Variavel ausente no .env: LANGSMITH_PROJECT")

    return problemas
