"""Bounded explicit reads override stale conversational suggestions, after input guard."""
import re
import unicodedata

from app.modules.operational.tools import OperationalDecision
from app.modules.rh.tools import BuscarOutrosUsuariosArgs, RhToolDecision
from app.modules.agenda.tools import AgendaToolDecision, ConsultarEventosArgs


def explicit_read(message):
    text="".join(c for c in unicodedata.normalize("NFKD",message.casefold()) if not unicodedata.combining(c)).strip().rstrip(".!? ")
    verbs=r"(?:mostre|liste|exiba|quero ver) "
    if text in {"liste meus eventos e treinamentos disponiveis com a referencia para selecionar um",
                "liste meus eventos com a referencia evento_id para eu selecionar um resumo"}:
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
    match=re.fullmatch(r"(?:faca|mostre|quero) (?:um |o )?resumo (?:do |de |desse |deste )?(?:(?P<tipo>treinamento|evento) )?(?P<selecao>.+)",text)
    if not match and not pending:return None
    selection=(match['selecao'] if match else text).strip('"\' ')
    number=re.fullmatch(r"(?:referencia |evento |treinamento )?(\d+)",selection)
    valid=[r for r in references if type(r.get("evento_id")) is int and isinstance(r.get("titulo"),str)]
    matches=[r for r in valid if r["evento_id"]==int(number[1])] if number else [r for r in valid if normalize(r["titulo"])==selection]
    ids={r["evento_id"] for r in matches}
    if len(ids)==1:
        reference=ids.pop()
        if match and match['tipo']=='evento':
            return OperationalDecision(acao="consultar_detalhes_evento",filtros={"identificador":str(reference)})
        return OperationalDecision(acao="consultar_resultados_treinamento",filtros={"evento_id":reference})
    if len(ids)>1:
        return "Há mais de um evento com esse nome. Escolha pela referência numérica exibida na listagem."
    if match or (pending and number):
        return "Selecione uma referência de evento exibida na sua listagem. Se não estiver nela, consulte seus eventos primeiro."
    return None


def distribution_pdf(message,previous):
    """Re-query the saved distribution; history text is not a data source."""
    text="".join(c for c in unicodedata.normalize("NFKD",message.casefold()) if not unicodedata.combining(c)).strip().rstrip(".!? ")
    if not re.fullmatch(r"(?:gere|crie) um pdf da distribuicao da minha equipe por cargo usando os dados autorizados desta conversa",text):
        return None
    if not isinstance(previous,dict) or previous.get("acao")!="consultar_indicadores_equipe":
        return "Consulte primeiro a distribuição da equipe por cargo nesta conversa para selecionar os filtros do PDF."
    try:
        decision=OperationalDecision.model_validate(previous)
    except ValueError:
        return "Não consegui recuperar os filtros autorizados da distribuição. Consulte a distribuição novamente antes de pedir o PDF."
    if decision.filtros.agrupar_por!="cargo":
        return "A consulta anterior não está agrupada por cargo. Consulte a distribuição por cargo antes de pedir este PDF."
    filters=decision.filtros.model_dump()
    filters.update(pagina=1,limite=50,tipo_grafico="grafico_barras")
    return OperationalDecision(acao=decision.acao,filtros=filters,
                               referencia_listagem_rh=decision.referencia_listagem_rh)
