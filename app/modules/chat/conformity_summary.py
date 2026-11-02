"""Translate a general conformity question into a bounded, authorized read."""
import re
import unicodedata


def asks_company_conformity(message):
    text = ''.join(c for c in unicodedata.normalize('NFKD', message.casefold()) if not unicodedata.combining(c))
    text = ' '.join(text.split()).strip().rstrip('.!? ')
    question = r"(?:(?:ta|esta|esta tudo|ta tudo|tudo esta|tudo ta) (?:tudo )?(?:nos conformes|em conformidade|em dia))"
    scope = r"(?:(?:na|da) (?:minha |nossa )?(?:empresa|equipe|unidade)|no (?:meu |nosso )?workspace)"
    return bool(re.fullmatch(question + r"(?: " + scope + r")?", text)
                or re.fullmatch(r"(?:a )?(?:minha |nossa )?empresa (?:inteira )?" + question, text)
                or re.fullmatch(r"empresa inteira e (?:eu )?quero saber se " + question, text))


def format_company_conformity(result, role):
    """Only summarize backend indicators; zero counts never certify legal compliance."""
    scope = ("Consultei a equipe autorizada do seu workspace. Isso não representa uma auditoria de toda a empresa."
             if role == 'GESTOR_WORKSPACE' else
             "Seu acesso de gestor permite consultar a equipe autorizada da sua unidade, não a empresa inteira.")
    metrics = {item['chave']: item for item in result['cartoes']['indicadores']}
    required = metrics['pares_aplicaveis']['valor']
    valid = metrics['vigentes']['valor']
    review = metrics['em_analise']['valor']
    scheduled = metrics['agendados']['valor']
    missing = metrics['sem_encaminhamento']['valor']
    if not required:
        summary = 'Não há exigências de NR no conjunto cadastral consultado. Isso não permite concluir que está tudo em conformidade.'
    else:
        summary = (f'Das {required} exigências de NR vinculadas aos colaboradores, {valid} têm registro vigente. '
                   f'Entre as demais, {review} estão em análise, {scheduled} têm atividade agendada e '
                   f'{missing} estão sem encaminhamento. Cada colaborador pode ter mais de uma exigência.')
    next_step = ('Priorize as exigências sem encaminhamento e acompanhe as análises e atividades agendadas.'
                 if review or scheduled or missing else
                 'Continue acompanhando vencimentos e a atualização dos registros.')
    return (scope + '\n\n' + summary + '\n\n' + next_step
            + ' O cadastro não certifica conformidade legal ou aptidão. A avaliação completa exige verificar também as obrigações e evidências aplicáveis com a equipe de SST.')
