"""Normalização limitada de formato; nunca autoriza uma ferramenta."""

import re

from app.modules.chat.schemas import MemorySearch
from app.modules.roteador.tools import (
    ConsultarAcessosArgs, ConsultarConversasArgs, ConsultarNotificacoesArgs,
    EnviarMensagemArgs,
)


ROUTER_CLARIFICATION = (
    "Não consegui identificar com segurança o que você quer fazer. "
    "Pode reformular o pedido? Nenhuma ação foi executada nesta tentativa."
)
_PREFIXES = "ROUTE|MEMORY|MESSAGE|CONVERSATION|NOTIFICATIONS|ACCESSES"
_COMMAND = re.compile(rf"\b(?:{_PREFIXES})\s*=", re.IGNORECASE)
_TOOLS = {
    "MEMORY": MemorySearch,
    "MESSAGE": EnviarMensagemArgs,
    "CONVERSATION": ConsultarConversasArgs,
    "NOTIFICATIONS": ConsultarNotificacoesArgs,
    "ACCESSES": ConsultarAcessosArgs,
}


def normalize_router_reply(text: str) -> str:
    value = text.strip().lstrip("\ufeff")
    fenced = re.fullmatch(r"```(?:json|text)?\s*(.*?)\s*```", value, re.DOTALL | re.I)
    if fenced:
        value = fenced.group(1).strip()
    first, _, tail = value.partition("\n")
    route = re.fullmatch(r"ROUTE\s*=\s*(rh|sst|agenda|faq|fora_escopo)", first.strip(), re.I)
    if route:
        # Uma pergunta/comentário após UMA rota não invalida a intenção. Descarte
        # o texto extra; o especialista responderá com as evidências autorizadas.
        # Duas decisões ou comandos de ferramenta continuam ambíguos e rejeitados.
        if _COMMAND.search(tail):
            raise ValueError("Mais de uma decisao de roteamento.")
        return "ROUTE=" + route.group(1).lower()
    tool = re.fullmatch(rf"({_PREFIXES})\s*=\s*(\{{.*\}})", value, re.DOTALL | re.I)
    if tool and tool.group(1).upper() in _TOOLS:
        prefix = tool.group(1).upper()
        args = _TOOLS[prefix].model_validate_json(tool.group(2))
        if prefix == "MESSAGE":
            args.confirmar_envio = False
        return prefix + "=" + args.model_dump_json()
    if not value or _COMMAND.search(value) or re.match(rf"^(?:{_PREFIXES})\b", value, re.I):
        raise ValueError("Decisao de roteamento invalida.")
    return value
