import re


# Contrato com o frontend: o conteúdo entre parênteses é o texto da ação, não uma URL.
GOOGLE_CALENDAR_CONNECT_ID = "google-calendar-conectar"
GOOGLE_CALENDAR_CONNECT_ACTION = f"[{GOOGLE_CALENDAR_CONNECT_ID}](Conectar minha conta Google)"

_CONNECT_ACTION = re.compile(r"\[google-calendar-conectar\]\([^\n)]*\)")
_ENDPOINT = (
    r"(?:https?://[^/\s`<>\[\]()]+)?/integracoes/google-calendar/"
    r"(?:conectar|status)/?(?:\?[^\s`<>\[\]()]+)?(?![\w/-])"
)
_ENDPOINT_LINK = re.compile(r"\[[^\]\n]+\]\(\s*" + _ENDPOINT + r"\s*\)")
_RAW_ENDPOINT = re.compile(r"`?" + _ENDPOINT + r"`?")


def normalizar_acoes_interface(text: str) -> str:
    """Não entrega URLs técnicas/geradas pelo modelo como ações de conexão."""
    text = _ENDPOINT_LINK.sub(lambda _: GOOGLE_CALENDAR_CONNECT_ACTION, text)
    text = _RAW_ENDPOINT.sub(lambda _: GOOGLE_CALENDAR_CONNECT_ACTION, text)
    return _CONNECT_ACTION.sub(lambda _: GOOGLE_CALENDAR_CONNECT_ACTION, text)
