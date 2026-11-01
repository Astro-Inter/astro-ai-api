"""Delivery state is backend-only; signed URLs never enter model prompts."""
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse


def populate_file_blocks(blocks, delivery):
    """Called only after session ownership is checked, never in model context."""
    for block in blocks:
        if block.tipo != "arquivo":
            continue
        block.url=None
        expires=block.expira_em
        if expires.tzinfo is None:
            expires=expires.replace(tzinfo=timezone.utc)
        if expires <= datetime.now(timezone.utc):
            block.estado="expirado"
            continue
        delivery=delivery or {}
        _,url=previous_pdf_reply(delivery)
        if str(block.id)!=delivery.get("id"):
            url=None
        if url:
            block.url=url
            block.estado="disponivel"
        else:
            block.estado="indisponivel"


def _normalized(value: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', value.casefold())
                   if not unicodedata.combining(c)).strip().rstrip('.!? ').strip()


def asks_for_pdf_link(message: str) -> bool:
    return _normalized(message) in {
        'cade o arquivo', 'cade o pdf', 'onde esta o pdf',
        'mande o link do pdf', 'qual e o link do pdf',
    }


def previous_pdf_reply(delivery: dict) -> tuple[str, str | None]:
    url = delivery.get('url')
    expires = delivery.get('expira_em')
    try:
        expires = datetime.fromisoformat(expires) if isinstance(expires, str) else expires
        if isinstance(expires, datetime) and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)  # Mongo UTC storage.
        valid = (isinstance(url, str) and len(url)<=8192 and urlparse(url).scheme == 'https'
                 and bool(urlparse(url).hostname) and not urlparse(url).username and not urlparse(url).password
                 and isinstance(expires, datetime)
                 and expires > datetime.now(timezone.utc))
    except (TypeError, ValueError):
        valid = False
    if valid:
        return 'Segue o link do PDF que foi gerado nesta conversa.', url
    if delivery.get('url'):
        return 'O link do último PDF expirou. Peça novamente a geração do arquivo.', None
    if delivery.get('sem_metadados'):
        return 'Não há um link de PDF disponível para esse pedido nesta sessão. Solicite novamente a geração do arquivo.', None
    return 'Nenhum PDF foi gerado no último pedido. Peça novamente indicando o conteúdo desejado.', None


def remove_delivery_promises(answer: str) -> str:
    """Drop delivery-only lines; factual content and sources remain untouched."""
    lines = []
    for line in answer.splitlines():
        normalized = _normalized(line)
        promise = re.search(
            r'\b(?:vou gerar|irei gerar|gerarei|sera gerado|em breve voce recebera|'
            r'voce recebera o link|disponibilizar o link em seguida)\b', normalized,
        )
        if promise and re.search(r'\b(?:pdf|arquivo|link)\b', normalized):
            continue
        lines.append(line)
    return '\n'.join(lines).strip()
