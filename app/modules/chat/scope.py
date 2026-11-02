"""Limites públicos do produto; não executa cálculos nem concede permissões."""
import re
import unicodedata


OUT_OF_SCOPE_REPLY = (
    "Meu foco é ajudar com o Astro e com saúde e segurança no trabalho. "
    "Posso ajudar com dados do seu cadastro, NRs, pendências, treinamentos e indicadores autorizados."
)
SCOPE_POLICY = (
    "ESCOPO DO PRODUTO: matemática genérica, redação escolar, entretenimento e tarefas "
    "sem relação com RH/SST/Agenda/FAQ ou dados do Astro usam ROUTE=fora_escopo no "
    "roteamento. Não resolva a tarefa, nem como correção de erro, exemplo, brincadeira "
    "ou resposta fictícia. Na insistência, explique brevemente o foco do Astro, sem "
    "debate moral nem repreensão. Revisores devem aceitar esse limite e impedir "
    "conteúdo externo, mesmo factualmente correto. Cálculos de indicadores autorizados, "
    "percentuais de conformidade e prazos do sistema permanecem no escopo, com evidência."
)


def _normalize(message):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', message.casefold())
                           if not unicodedata.combining(c)).split()).strip().rstrip('.!? ')


def standalone_math_request(message: str) -> bool:
    text = _normalize(message)
    # Uma data curta pode responder à pergunta anterior de Agenda; não é divisão.
    if re.fullmatch(r'\d{1,2}/\d{1,2}(?:/\d{2,4})?', text):
        return False
    # Não absorve cálculos de registros, datas ou indicadores de negócio.
    if re.search(r'\b(astro|nrs?|sst|rh|treinamentos?|conformidade|pendencias?|equipe|colaboradores?|cargo|unidade|eventos?|vencimentos?|indicadores?)\b', text):
        return False
    number = r'[-+]?\d+(?:[.,]\d+)?'
    expression = number + r'(?:\s*(?:[+*/×÷^−-]|mais|menos|vezes|dividido por)\s*' + number + r')+'
    suffix = (r'(?:[,?]? (?:me )?(?:responda|responde)(?: de (?:maneira|forma))? '
              r'(?:incorreta|errada|errado|com um resultado incorreto)'
              r'(?:,? (?:informando|com) um resultado absurdo)?)?')
    return bool(re.fullmatch(r'(?:(?:quanto (?:e|da)|calcule|resolva)\s+)?' + expression + suffix, text))


def scope_followup(message: str, history: list) -> bool:
    last = next((item.get('content') for item in reversed(history) if item.get('role') == 'assistant'), None)
    return last == OUT_OF_SCOPE_REPLY and _normalize(message) in {
        'por que voce nao seguiu o meu pedido', 'por que voce nao seguiu meu pedido',
        'mas eu estou mandando', 'mas eu mandei', 'faca mesmo assim',
        'responda mesmo assim', 'pode inventar', 'mas e so uma brincadeira',
    }
