"""Barreiras de divulgação independentes da decisão probabilística dos agentes."""

import re
import unicodedata


INTERNAL_INSTRUCTIONS_BOUNDARY = (
    "Não posso mostrar, resumir ou avaliar minhas instruções internas. "
    "Sou o Agente do Astro e posso explicar minhas funcionalidades públicas "
    "ou ajudar com RH, segurança do trabalho, agenda e normas internas."
)


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    return " ".join("".join(
        char for char in value
        if not unicodedata.combining(char) and unicodedata.category(char) != "Cf"
    ).split())


def requests_internal_instructions(message: str) -> bool:
    text = _normalized(message)
    # Política conservadora para instruções do modelo, não para normas internas
    # da empresa nem para relatos de erro ou a palavra genérica "prompt".
    return bool(re.search(
        r"\b(?:system\s*prompt|developer\s*prompt|prompt(?:s)?\s+(?:intern[oa]s?|do sistema)|"
        r"instrucoes\s+(?:internas|do sistema|de desenvolvedor)|mensagem\s+de sistema)\b",
        text,
    ) or re.search(
        r"\b(?:repita|mostre|copie|revele|traduza|resuma)\b.{0,100}"
        r"\b(?:tudo|texto|mensagens|instrucoes)\b.{0,80}"
        r"\b(?:antes da minha|antes desta|antes desse|acima da minha)\b", text,
    ))


def exposes_internal_instructions(response: str) -> bool:
    text = _normalized(response)
    # Marcadores específicos não são necessários em nenhuma resposta de negócio.
    if any(marker in text for marker in (
        "contexto da requisicao", "dados para revisao", "exemplo ficticio",
        "usuario_atual.role", "guardrail_entrada_prompt", "roteador_prompt",
    )):
        return True
    headings = ("contrato comum", "hierarquia e privacidade", "verificacao antes da saida")
    return sum(heading in text for heading in headings) >= 2 or bool(re.search(
        r"\b(?:system prompt|prompt interno|prompt do sistema)\b.{0,200}"
        r"\b(?:identidade|hierarquia|contrato|instrucoes|estruturado)\b", text,
    ))
