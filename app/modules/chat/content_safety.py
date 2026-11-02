"""Última barreira local para geração abusiva; não classifica pessoas pelo nome."""
import re
import unicodedata

CONTENT_BOUNDARY = (
    "Não posso ajudar com exaltação extremista ou repetição abusiva de mensagens. "
    "Posso orientar sobre prevenção e relato de discriminação no trabalho."
)


def unsafe_generated_text(text: str) -> bool:
    value = ' '.join(''.join(c for c in unicodedata.normalize('NFKD', text.casefold())
                            if not unicodedata.combining(c)).split())
    # Verifica a afirmação gerada, não a simples presença de um nome em cadastro
    # ou denúncia. Uma orientação explícita de denúncia pode contextualizar citação.
    praise = re.search(
        r'(?:(?:adolf )?hitler[, :]+(?:eu )?(?:amo|adoro|venero) (?:voce|vc)|'
        r'(?:eu )?(?:amo|adoro|venero) (?:o )?(?:adolf )?hitler\b|'
        r'(?:viva|gloria a|salve) (?:o )?(?:nazismo|hitler)\b)', value)
    report_guidance = re.match(r'(?:voce pode denunciar|para denunciar|como relatar|voce pode relatar|ao registrar uma denuncia)\b', value)
    if praise and not report_guidance:
        return True
    # Saídas geradas em loop não devem virar mensagem, memória ou PDF.
    return bool(re.search(r'(.{8,200}?[.!?])(?:\s*\1){5,}', value))
