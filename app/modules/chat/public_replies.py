"""Public product facts, not generated claims about unavailable operations."""
import re
import unicodedata


CAPABILITIES = (
    "Sou o Agente do Astro. Posso consultar seus dados cadastrais, funcionários "
    "no seu escopo de acesso, NRs, conformidade, eventos e treinamentos atribuídos, "
    "notificações, dias de acesso e conversas. Também posso buscar informações "
    "nos documentos do Astro e gerar um PDF com o conteúdo consultado. "
    "O envio de mensagens e a criação de eventos no seu Google Calendar exigem "
    "prévia e confirmação. A conexão Google é opcional. "
    "Não realizo inscrições em treinamentos nem crio eventos internos do Astro."
)


def public_reply(message: str) -> str | None:
    normalized = ''.join(c for c in unicodedata.normalize('NFKD', message.casefold())
                         if not unicodedata.combining(c))
    normalized = re.sub(r'\s+', ' ', normalized).strip().rstrip('.!? ').strip()
    if re.fullmatch(r'(?:oi|ola|bom dia|boa tarde|boa noite)(?:,? tudo bem)?', normalized):
        return 'Olá! Sou o Agente do Astro. Como posso ajudar hoje?'
    if normalized in {
        'quem e voce', 'quem e vc', 'e quem e voce', 'quem e voce dentro do astro',
        'qual e a sua funcao', 'qual e sua funcao', 'oi, qual e a sua funcao',
        'oi quem e voce', 'oi tudo bem, quem e voce',
        'quem e voce, e o que voce faz', 'o que voce faz',
        'voce e o roteador do astro ou o agente do astro',
        'como o astro pode me ajudar', 'com quais assuntos voce pode me ajudar',
        'e com oq vc pode me ajudar',
        'explique brevemente o que voce consegue consultar no sistema',
    }:
        return CAPABILITIES
    if normalized in {'o que sao normas regulamentadoras', 'o que sao nrs', 'oq sao nr'}:
        return (
            'As Normas Regulamentadoras (NRs) complementam as regras de segurança e saúde '
            'do trabalho da CLT. Elas definem deveres e direitos de empregadores e '
            'trabalhadores para prevenir acidentes e doenças ocupacionais. '
            'A aplicação de cada NR depende de seu campo de aplicação e das atividades '
            'e riscos envolvidos; não significa que todas as NRs se apliquem a qualquer cargo. '
            'Para os vínculos registrados no Astro, pergunte quais NRs estão associadas '
            'ao seu cargo, unidade ou empresa.\n\n'
            'Fonte: [Ministério do Trabalho e Emprego]('
            'https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/'
            'seguranca-e-saude-no-trabalho/ctpp-nrs/normas-regulamentadoras-nrs).'
        )
    return None
