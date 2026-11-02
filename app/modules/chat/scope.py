"""Limites públicos do produto; não executa cálculos nem concede permissões."""
import re
import unicodedata


OUT_OF_SCOPE_REPLY = (
    "Meu foco é ajudar com o Astro e com saúde e segurança no trabalho. "
    "Posso ajudar com dados do seu cadastro, NRs, pendências, treinamentos e indicadores autorizados."
)
COMPANY_CONTEXT_REPLY = (
    "Posso ajudar com uma situação concreta de trabalho ou uma consulta no Astro. "
    "Você quer relatar algum problema de segurança ou RH?"
)
SCOPE_POLICY = (
    "ESCOPO DO PRODUTO: matemática genérica, redação escolar, entretenimento e tarefas "
    "sem relação com RH/SST/Agenda/FAQ ou dados do Astro usam ROUTE=fora_escopo no "
    "roteamento. Piadas, charadas, poemas e ficção são entretenimento, mesmo quando "
    "mencionam SST ou o Astro. Não confunda relatos de piadas ofensivas/assédio com "
    "pedido de humor: relatos de risco continuam em SST. Não resolva tarefa externa, "
    "nem como correção de erro, exemplo, brincadeira "
    "ou resposta fictícia. Na insistência, explique brevemente o foco do Astro, sem "
    "debate moral nem repreensão. Revisores devem aceitar esse limite e impedir "
    "conteúdo externo, mesmo factualmente correto. Cálculos de indicadores autorizados, "
    "percentuais de conformidade e prazos do sistema permanecem no escopo, com evidência."
    " Declarações afetivas pessoais, textos para copiar em massa e repetição mecânica "
    "também estão fora do escopo, mesmo que o destinatário seja chamado de gestor. "
    "Histórico contendo uma resposta inadequada não autoriza continuá-la."
    " Listas de exercícios escolares e prática de tabuada/multiplicação também "
    "são tarefas externas, mesmo sem gabarito. 'Treinar' matemática não é consultar "
    "treinamentos atribuídos de SST."
    " Receitas culinárias e avaliações genéricas da reputação de empresas são externas. "
    "Não ofereça redigir críticas, elogios ou rankings de empresas. Uma insatisfação "
    "sem contexto pode pedir uma pergunta neutra sobre a situação de trabalho. "
    "Relatos de assédio, discriminação e riscos devem receber apoio sem julgar a empresa. "
    "Segurança no preparo de alimentos não é ensinar uma receita culinária."
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


def entertainment_request(message: str, history: list) -> bool:
    """Only explicit creative requests, never reports mentioning jokes or harassment."""
    text = _normalize(message)
    text = re.sub(r',? por favor$', '', text).strip()
    creative = (
        r'(?:(?:agora |ai )?(?:me )?(?:conte|conta|fale|fala|mande|manda|diga|escreva|crie|faca|invente) '
        r'(?:me )?(?:(?:um|uma|outra|outro) )?(?:piada|charada|poema|poesia|conto|fabula)\b'
        r'|(?:quero|gostaria de) (?:ouvir|ler) (?:uma? )?(?:piada|charada|poema|conto)\b)'
    )
    if re.match(creative, text):
        return True
    # "uma sobre futebol também" só seleciona humor se já era esse o pedido.
    user_turns = [item.get('content', '') for item in history if item.get('role') == 'user']
    followup_pattern = r'(?:(?:agora )?(?:me )?(?:fale|conte|mande|diga) )?(?:uma|outra)(?: sobre [\w ]{1,100})?(?: tambem)?'
    previous_is_humor = bool(user_turns and re.match(creative, _normalize(user_turns[-1])))
    if (not previous_is_humor and len(user_turns) >= 2
            and re.fullmatch(followup_pattern, _normalize(user_turns[-1]))):
        previous_is_humor = bool(re.match(creative, _normalize(user_turns[-2])))
    return bool(re.fullmatch(followup_pattern, text) and previous_is_humor)


def mass_repetition_request(message: str) -> bool:
    text = _normalize(message)
    # Relatos e perguntas de prevenção não são instruções para gerar spam.
    if re.match(r'(?:como (?:denunciar|relatar|evitar)|recebi|sofri|me enviaram|meu colega repetiu)\b', text):
        return False
    match = re.search(r'\b(?:repita|repete|repetir|escreva|escreve|diga|fale|fala)\b.{0,180}?\b(\d{1,6}|cem|mil) vezes\b', text)
    return bool(match and (match[1] in {'cem', 'mil'} or int(match[1]) >= 20))


def school_exercise_request(message: str) -> bool:
    """Pedido de material para praticar matemática; não confunde treinamento SST."""
    text = _normalize(message)
    action = re.match(r'(?:agora |por favor,? )?(?:(?:me )?(?:faca|crie|monte|gere|prepare|escreva|passe)|quero)\b', text)
    practice = re.search(r'\b(?:exercicios?|atividades?|lista|tabuada|treinar|praticar)\b', text)
    subject = re.search(r'\b(?:matematica|multiplicacao|divisao|tabuada|algebra|equacoes?|fracoes?|aritmetica)\b', text)
    return bool(action and practice and subject)


def external_topic_reply(message: str) -> str | None:
    """Pedidos explícitos de culinária/opinião, sem catálogo de marcas proibidas."""
    text = _normalize(message)
    text = re.sub(r'^(?:ok|certo|beleza|blz)[,!]?\s+', '', text)
    # Referências a perigo ou condições de trabalho mantêm a interpretação SST/RH.
    work_issue = re.search(r'\b(?:assedio|discriminacao|acidente|queimadura|risco|riscos|seguranca|epi|epis|nr|nrs|sst|salario|salarios|direitos|insalubridade|denuncia|denunciar|contaminacao|conformidade|indicadores?)\b|\breceita medica\b|\bexame ocupacional\b', text)
    if work_issue:
        return None
    cooking = re.match(
        r'(?:(?:me )?(?:ajuda|ajude) com|(?:me )?(?:de|da|passe|mande|ensine|faca|crie|mostre)|quero|como (?:fazer|preparar)) '
        r'(?:(?:uma|um|a|o) )?(?:receita(?:s)?(?: de| para)?|bolo|torta|pudim|lasanha|pao|brigadeiro)\b', text)
    if cooking:
        return OUT_OF_SCOPE_REPLY
    ranking = re.search(r'\b(?:melhor|pior) empresa(?: do mundo| do brasil)?\b', text)
    opinion = re.match(r'(?:(?:me )?(?:ajude|ajuda) a (?:escrever|formular)|(?:escreva|faca|crie)) '
                       r'(?:(?:uma|um) )?(?:critica|elogio|ranking) (?:a|da|das|de|sobre)\b', text)
    if ranking or opinion:
        return OUT_OF_SCOPE_REPLY
    if re.fullmatch(r'(?:eu )?(?:odeio|detesto) (?:a empresa |a |o )[\w&.-]+(?: [\w&.-]+){0,3}', text):
        return COMPANY_CONTEXT_REPLY
    return None
