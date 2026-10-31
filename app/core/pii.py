"""Pseudonimização local dos dados enviados à IA; mapas vivem só na chamada."""

import json
import re
import secrets
from typing import Any


_TOKEN = re.compile(r"\[ASTRO_PII_[0-9a-f]{12}_[A-Z]+_\d+\]")
_PATTERNS = (
    ("EMAIL", re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w-])")),
    ("CNPJ", re.compile(r"(?<!\w)(?:\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{14})(?!\w)")),
    ("CPF", re.compile(r"(?<!\w)(?:\d{3}\.\d{3}\.\d{3}-\d{2}|\d{11})(?!\w)")),
    ("TELEFONE", re.compile(r"(?<!\w)(?:\+55\s*)?\(\d{2}\)\s*\d{4,5}[-\s]\d{4}(?!\d)")),
    ("TELEFONE", re.compile(r"(?<!\w)(?:\+55\s*)?\d{2}\s+\d{4,5}-\d{4}(?!\d)")),
    ("TELEFONE", re.compile(r"(?i)\b(?:telefone|celular|whatsapp)\s*:\s*(\+?[\d ()-]{8,22}\d)")),
    ("CEP", re.compile(r"(?i)\bCEP\s*:?\s*(\d{5}-?\d{3})(?!\d)")),
    ("CEP", re.compile(r"\b\d{5}-\d{3}\b")),
    ("PESSOA", re.compile(r"\b(?:[Mm]eu nome [ée]|[Mm]e chamo)\s+([A-ZÀ-Ý][a-zà-ÿ]+(?:\s+[A-ZÀ-Ý][a-zà-ÿ]+){0,4})")),
    ("PESSOA", re.compile(r"(?im)\b(?:nome|nome completo)\s*:\s*([^\n,;|.]{2,120})")),
)
_SECRET = re.compile(
    r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/-]+=*|"
    r"(\b(?:senha|password|api[_-]?key|access[_-]?token|refresh[_-]?token|secret)\s*[:=]\s*)[^\s,;\"}]+"
)
_JSON_SECRET = re.compile(r'(?i)("(?:senha|password|api_key|access_token|refresh_token|secret|authorization)"\s*:\s*)"(?:\\.|[^"\\])*"')
_CREDENTIAL = re.compile(r"\b(?:gsk_[A-Za-z0-9]{12,}|sk-[A-Za-z0-9_-]{12,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b")
_PRIVATE_IDS = {"uid", "firebase_uid", "id_user", "id_usuario", "session_id", "workspace_id", "id_envia", "id_recebe", "usuario_id", "sessao_id", "remetente_id", "destinatario_id", "mensagem_id"}
_PERSON_FIELDS = {"nome", "nome_completo", "destinatario_nome", "remetente_nome", "nome_usuario"}
_PERSONAL_KINDS = {"endereco_residencial": "ENDERECO", "endereço_residencial": "ENDERECO",
                   "cpf": "CPF", "cnpj": "CNPJ", "telefone": "TELEFONE", "celular": "TELEFONE",
                   "cep": "CEP", "email": "EMAIL", "e-mail": "EMAIL"}
_SECRET_FIELDS = {"senha", "password", "access_token", "refresh_token", "api_key", "secret", "authorization"}


class PiiTokenizer:
    def __init__(self):
        self._scope = secrets.token_hex(6)
        self._values: dict[str, str] = {}
        self._tokens: dict[tuple[str, str], str] = {}
        self._known_names: set[str] = set()

    def __repr__(self):
        return "PiiTokenizer(<mapa privado>)"

    @property
    def has_tokens(self):
        return bool(self._values)

    def _token(self, kind: str, value: str) -> str:
        # CPF/CNPJ com e sem pontuação representam a mesma evidência.
        identity = re.sub(r"\D", "", value) if kind in {"CPF", "CNPJ", "TELEFONE", "CEP"} else value
        key = kind, identity
        if key not in self._tokens:
            token = f"[ASTRO_PII_{self._scope}_{kind}_{len(self._tokens) + 1}]"
            self._tokens[key] = token
            self._values[token] = value
        return self._tokens[key]

    def seed(self, value: Any, *, person: bool = False):
        """Nomes só são reconhecidos em registros pessoais, nunca por todo 'nome'."""
        if isinstance(value, dict):
            person = person or any(str(key).lower() in {"cpf", "email", "firebase_uid", "id_usuario", "usuario_id"} for key in value)
            for key, item in value.items():
                if key in _PERSON_FIELDS and (person or key != "nome") and isinstance(item, str) and 3 <= len(item) <= 200:
                    self._known_names.add(item)
                self.seed(item, person=key in {"usuarios", "destinatario", "remetente"})
        elif isinstance(value, list):
            for item in value:
                self.seed(item, person=person)

    def mask_text(self, text: str) -> str:
        text = _JSON_SECRET.sub(lambda m: m.group(1) + '"[SEGREDO_REMOVIDO]"', text)
        text = _SECRET.sub(lambda m: (m.group(1) or m.group(2)) + "[SEGREDO_REMOVIDO]", text)
        text = _CREDENTIAL.sub("[SEGREDO_REMOVIDO]", text)
        for name in sorted(self._known_names, key=len, reverse=True):
            text = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w)", lambda m: self._token("PESSOA", m.group()), text)
        for kind, pattern in _PATTERNS:
            def replace(match, kind=kind):
                if _TOKEN.search(match.group()):
                    return match.group()
                if match.lastindex:
                    start, end = match.span(1)
                    return match.group()[:start - match.start()] + self._token(kind, match.group(1)) + match.group()[end - match.start():]
                return self._token(kind, match.group())
            text = pattern.sub(replace, text)
        return text

    def mask_data(self, value: Any):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                normalized = key.lower() if isinstance(key, str) else ""
                if normalized in _PRIVATE_IDS and item is not None:
                    result[key] = "[IDENTIFICADOR_REMOVIDO]"
                elif normalized in _SECRET_FIELDS and item is not None:
                    result[key] = "[SEGREDO_REMOVIDO]"
                elif normalized in _PERSONAL_KINDS and isinstance(item, str) and item:
                    result[key] = self._token(_PERSONAL_KINDS[normalized], item)
                else:
                    result[key] = self.mask_data(item)
            return result
        if isinstance(value, list):
            return [self.mask_data(item) for item in value]
        return self.mask_text(value) if isinstance(value, str) else value

    def mask_messages(self, messages):
        # Não altera os objetos originais nem o histórico persistente.
        return [message.model_copy(update={"content": self.mask_text(message.content)}) for message in messages]

    def restore_text(self, text: str) -> str:
        if any(token not in self._values for token in _TOKEN.findall(text)):
            raise ValueError("Identificador pseudonimizado desconhecido.")
        return _TOKEN.sub(lambda match: self._values[match.group()], text)

    def restore_data(self, value):
        if isinstance(value, dict):
            return {key: self.restore_data(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.restore_data(item) for item in value]
        return self.restore_text(value) if isinstance(value, str) else value

    def restore_output(self, text: str) -> str:
        """Restaura valores JSON estruturalmente, sem injetar aspas ou campos."""
        raw = text.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL | re.IGNORECASE)
        command = re.fullmatch(r"([A-Z_]+\s*=\s*)(\{.*\})", raw, re.DOTALL)
        payload = fenced.group(1) if fenced else command.group(2) if command else raw
        try:
            decoded = json.loads(payload)
        except (ValueError, TypeError):
            return self.restore_text(text)
        restored = json.dumps(self.restore_data(decoded), ensure_ascii=False)
        return command.group(1) + restored if command else restored


def redact_for_embedding(text: str) -> str:
    tokenizer = PiiTokenizer()
    masked = tokenizer.mask_text(text)
    return _TOKEN.sub(lambda match: "[" + match.group().split("_")[-2] + "]", masked)


def redact_trace_data(value):
    tokenizer = PiiTokenizer()
    tokenizer.seed(value)
    masked = tokenizer.mask_data(value)

    def clean(item):
        if isinstance(item, str):
            return _TOKEN.sub("[DADO_REMOVIDO]", item)
        if isinstance(item, dict):
            return {key: clean(child) for key, child in item.items()}
        if isinstance(item, list):
            return [clean(child) for child in item]
        return item

    return clean(masked)
