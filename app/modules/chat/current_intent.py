"""Bounded explicit reads override stale conversational suggestions, after input guard."""
import re
import unicodedata

from app.modules.operational.tools import OperationalDecision
from app.modules.rh.tools import BuscarOutrosUsuariosArgs, RhToolDecision
from app.modules.agenda.tools import AgendaToolDecision, ConsultarEventosArgs


def explicit_read(message):
    text="".join(c for c in unicodedata.normalize("NFKD",message.casefold()) if not unicodedata.combining(c)).strip().rstrip(".!? ")
    verbs=r"(?:mostre|liste|exiba|quero ver) "
    if text=="liste meus eventos e treinamentos disponiveis com a referencia para selecionar um":
        return AgendaToolDecision(acao="consultar_eventos",filtros=ConsultarEventosArgs(proximos=False))
    scopes=r"(?:da |de |para a )?(?:(?:minha|nossa|a) )?equipe"
    requests=[
        (verbs+r"(?:os )?indicadores de cobertura "+scopes,"consultar_indicadores_cobertura",{}),
        (verbs+r"(?P<n>\d{1,7}) registros da matriz de conformidade "+scopes,"consultar_matriz_conformidade_equipe",{}),
        (verbs+r"(?:a )?matriz de conformidade "+scopes,"consultar_matriz_conformidade_equipe",{}),
        (r"compare (?:a )?conformidade entre (?:as )?unidades","comparar_conformidade_unidades",{}),
        (verbs+r"(?:a )?evolucao mensal das conclusoes "+scopes,"consultar_evolucao_conclusoes_equipe",{"tipo_grafico":"grafico_linhas"}),
        (r"quais evidencias aguardam (?:minha )?validacao","consultar_evidencias_pendentes_validacao",{}),
        (r"tenho treinamentos no mesmo horario","consultar_conflitos_agenda",{}),
        (verbs+r"(?:as )?lacunas sem encaminhamento por nr em barras","consultar_indicadores_cobertura",{}),
    ]
    for pattern,action,filters in requests:
        match=re.fullmatch(pattern,text)
        if match:
            filters=dict(filters)
            if match.groupdict().get("n"):
                n=int(match["n"])
                if not 1<=n<=1000000:return None
                filters["limite"]=n
            return OperationalDecision(acao=action,filtros=filters)
    match=re.fullmatch(verbs+r"(?P<n>\d{1,7}) usuarios "+scopes+r" em uma tabela",text)
    if match and 1<=int(match["n"])<=1000000:
        return RhToolDecision(acao="buscar_outros_usuarios",filtros=BuscarOutrosUsuariosArgs(limite=int(match["n"])))
    return None


def selected_event(message,references,pending=False):
    """Exact title/ID matches select a record; the Java API still authorizes it."""
    def normalize(text):
        return "".join(c for c in unicodedata.normalize("NFKD",text.casefold()) if not unicodedata.combining(c)).strip().rstrip(".!? ")
    text=normalize(message)
    match=re.fullmatch(r"(?:faca|mostre|quero) (?:um |o )?resumo (?:do |de |desse |deste )?(?:treinamento |evento )?(.+)",text)
    if not match and not pending:return None
    selection=(match[1] if match else text).strip('"\' ')
    number=re.fullmatch(r"(?:referencia |evento |treinamento )?(\d+)",selection)
    valid=[r for r in references if type(r.get("evento_id")) is int and isinstance(r.get("titulo"),str)]
    matches=[r for r in valid if r["evento_id"]==int(number[1])] if number else [r for r in valid if normalize(r["titulo"])==selection]
    ids={r["evento_id"] for r in matches}
    if len(ids)==1:
        return OperationalDecision(acao="consultar_resultados_treinamento",filtros={"evento_id":ids.pop()})
    if len(ids)>1:
        return "Há mais de um evento com esse nome. Escolha pela referência numérica exibida na listagem."
    if match or (pending and number):
        return "Selecione uma referência de evento exibida na sua listagem. Se não estiver nela, consulte seus eventos primeiro."
    return None
