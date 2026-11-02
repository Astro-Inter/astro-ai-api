"""Bounded explicit reads override stale conversational suggestions, after input guard."""
import re
import unicodedata

from app.modules.operational.tools import OperationalDecision
from app.modules.rh.tools import BuscarOutrosUsuariosArgs, RhToolDecision
from app.modules.agenda.tools import AgendaToolDecision, ConsultarEventosArgs


def personal_pending_kind(message):
    """Only complete personal requests; never infer a team or another identity."""
    text = ''.join(c for c in unicodedata.normalize('NFKD', message.casefold()) if not unicodedata.combining(c))
    text = ' '.join(text.split()).strip().rstrip('.!? ')
    text = re.sub(r',? por favor$', '', text).strip()
    request = r"(?:(?:eu )?tenho (?:algumas? )?pendencias?|quais (?:sao )?(?:as )?minhas pendencias|(?:mostre|liste) (?:as )?minhas pendencias)"
    if re.fullmatch(request, text):
        return 'esclarecer'
    if re.fullmatch(request + r" (?:de|em|com|nas|das) (?:(?:minhas|normas) )?(?:nrs?|conformidade|normas regulamentadoras)", text):
        return 'sst'
    return None


def general_nr_question(message):
    """Recognize a definition request, never a numbered NR or personal obligation."""
    text = ''.join(c for c in unicodedata.normalize('NFKD', message.casefold()) if not unicodedata.combining(c))
    text = ' '.join(text.split()).strip().rstrip('.!? ')
    text = re.sub(r',? por favor$', '', text).strip()
    noun = r"(?:(?:um|uma|as|os) )?(?:nrs?|normas? regulamentadoras?)"
    definition = r"(?:o que|o q|oq) (?:e|eh|sao|era|seria) " + noun
    explanation = r"(?:me )?(?:explique|explica|defina|fale sobre) (?:" + definition + "|" + noun + ")"
    ordinary = "(?:" + definition + "|" + explanation + ")"
    return bool(re.fullmatch(ordinary + r"(?: (?:em|no formato|para) (?:um )?pdf)?", text)
                or re.fullmatch(r"(?:gere|gera|crie|cria|faca|faz) (?:um )?(?:arquivo )?pdf "
                                r"(?:falando|explicando|sobre) (?:" + ordinary + "|" + noun + ")", text))


def explicit_read(message):
    text="".join(c for c in unicodedata.normalize("NFKD",message.casefold()) if not unicodedata.combining(c)).strip().rstrip(".!? ")
    verbs=r"(?:mostre|liste|exiba|quero ver) "
    if text in {"liste meus eventos e treinamentos disponiveis com a referencia para selecionar um",
                "liste meus eventos com a referencia evento_id para eu selecionar um resumo"}:
        return AgendaToolDecision(acao="consultar_eventos",filtros=ConsultarEventosArgs(proximos=False))
    chart = re.fullmatch(r"(?:(?:mostre|gere|faca|crie) (?:um )?)?grafico (?:de )?"
                         r"(?P<kind>pizza|setores|rosca|donut|barras) (?:de )?pendencias "
                         r"(?:dos? |da |de )?(?:colaboradores|funcionarios|minha equipe|nossa equipe|equipe)(?: por nr)?", text)
    if chart:
        kind = {"pizza": "grafico_setores", "setores": "grafico_setores", "rosca": "grafico_rosca",
                "donut": "grafico_rosca", "barras": "grafico_barras"}[chart['kind']]
        return OperationalDecision(acao="consultar_grafico_pendencias_nr", filtros={"tipo_grafico": kind})
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
