"""Bounded explicit reads override stale conversational suggestions, after input guard."""
import re
import unicodedata

from app.modules.operational.tools import OperationalDecision
from app.modules.rh.tools import BuscarOutrosUsuariosArgs, RhToolDecision
from app.modules.agenda.tools import AgendaToolDecision, ConsultarEventosArgs, ConsultarTreinamentosArgs


TEAM_PENDING_QUESTION = "Você quer verificar pendências de NR da sua equipe, de treinamentos ou de formulários?"


def team_pending_read(message, history=()):
    """Disambiguate the pending type once, then execute a current authorized read."""
    def normalize(value):
        return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', value.casefold())
                               if not unicodedata.combining(c)).split()).strip().rstrip('.!? ')

    text = normalize(message)
    generic = re.fullmatch(r"(?:(?:tem|ha|existe) (?:algum |alguns )?(?:colaborador(?:es)? meu(?:s)?|"
                           r"(?:dos )?meus colaboradores|pessoa(?:s)? da minha equipe)|"
                           r"tenho (?:algum |alguns )?colaborador(?:es)?) com pendencias?", text)
    if generic:
        return TEAM_PENDING_QUESTION
    last = history[-1] if history and history[-1].get('role') == 'assistant' else {}
    offer = normalize(last.get('content', ''))
    nr_only = offer in {
        'posso consultar as pendencias de nr da equipe autorizada no seu workspace. voce quer verificar pendencias de nr',
        'voce quer verificar pendencias de nr da sua equipe',
    }
    choices = offer == normalize(TEAM_PENDING_QUESTION)
    if nr_only and text in {'pode verificar', 'pode consultar', 'sim', 's', 'isso', 'ok', 'verifique', 'faca isso'}:
        return OperationalDecision(acao='consultar_pendencias_equipe', filtros={'limite': 50})
    if (nr_only or choices) and text in {'nr', 'nrs', 'de nr', 'pendencias de nr', 'conformidade', 'conformidades'}:
        return OperationalDecision(acao='consultar_pendencias_equipe', filtros={'limite': 50})
    if choices and text in {'pode verificar', 'pode consultar', 'sim', 's', 'isso', 'ok'}:
        return TEAM_PENDING_QUESTION
    return None


def conversational_read(message, history=()):
    """Resolve read subjects and the immediately preceding clarification locally."""
    def normalized(value):
        return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', value.casefold())
                               if not unicodedata.combining(c)).split()).strip().rstrip('.!? ')
    text = normalized(message)
    last = normalized(history[-1].get('content', '')) if history and history[-1].get('role') == 'assistant' else ''
    if (text in {'quais sao as atividades pendentes', 'quais atividades estao pendentes'}
            and last.startswith('estas sao as nrs do cargo ')):
        return AgendaToolDecision(acao='consultar_eventos', filtros=ConsultarEventosArgs(
            situacao='a_realizar', proximos=False))
    if (text in {'e treinamentos', 'e os treinamentos'}
            and re.search(r'\b(voce|seus|suas)\b', last)
            and re.search(r'pendenc|eventos|atividades|treinamentos', last)
            and not re.search(r'\b(equipe|workspace|empresa)\b', last)):
        return AgendaToolDecision(acao='consultar_treinamentos', filtros=ConsultarTreinamentosArgs())
    personal_training = re.fullmatch(
        r"(?:(?:liste|mostre|quero ver|quero consultar|consulte) )?(?:os )?(?:proximos )?"
        r"(?:meus treinamentos|treinamentos atribuidos (?:a mim|a voce)|treinamentos que preciso (?:fazer|realizar))", text)
    if personal_training or (text in {'treinamentos', 'os treinamentos', 'os proximos treinamentos'}
                             and 'treinamentos' in last and 'eventos' in last and '?' in history[-1].get('content', '')):
        return AgendaToolDecision(acao='consultar_treinamentos', filtros=ConsultarTreinamentosArgs())
    if text in {'eventos', 'os eventos', 'os proximos eventos', 'os proximos eventos da agenda'} and 'treinamentos' in last and 'eventos' in last:
        return AgendaToolDecision(acao='consultar_eventos', filtros=ConsultarEventosArgs())
    if re.fullmatch(r"(?:me (?:fala|fale|mostre)|mostre|liste|consulte) (?:as )?pendencias de nr da minha equipe", text):
        return OperationalDecision(acao='consultar_pendencias_equipe', filtros={'limite': 50})
    if text in {'de colaboradores por cargo', 'colaboradores por cargo', 'colaborar por cargo'} and 'grafico' in last:
        return OperationalDecision(acao='consultar_indicadores_equipe', filtros={'tipos': ['COLABORADOR'], 'limite': 50})
    return None


def people_count_read(message, history=()):
    """Counts current authorized records; never interprets a named company as scope."""
    def normalize(value):
        return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', value.casefold())
                               if not unicodedata.combining(c)).split()).strip().rstrip('.!? ')

    def parse(text):
        match = re.fullmatch(
            r"(?P<people>quantas pessoas|quantos usuarios|quantos colaboradores|quantos funcionarios) "
            r"(?:(?:tem|ha|existem|estao cadastrados) )?"
            r"(?:no|na|do|da|neste|nesta|nesse|nessa) (?:(?:meu|minha|nosso|nossa|seu|sua) )?"
            r"(?P<scope>workspace|unidade)", text)
        return match

    text = normalize(message)
    match = parse(text)
    scope = match['scope'] if match else None
    collaborators = bool(match and match['people'] in {'quantos colaboradores', 'quantos funcionarios'})
    if not match and text in {"isso mesmo, faca isso", "isso emsmo, faca isso", "isso", "ok", "sim", "pode consultar", "faca isso"}:
        if len(history) >= 2 and history[-1].get('role') == 'assistant' and history[-2].get('role') == 'user':
            offer = normalize(history[-1].get('content', ''))
            previous = parse(normalize(history[-2].get('content', '')))
            if previous and offer == (
                "nao consigo confirmar o total de pessoas no workspace. posso ajudar a consultar "
                "quantos colaboradores ha na sua unidade, se esse for o dado que voce precisa"):
                scope, collaborators = 'unidade', True
    if scope is None:
        return None
    filters = BuscarOutrosUsuariosArgs(consulta='contagem', escopo='unidade' if scope == 'unidade' else 'autorizado',
                                      tipos=['COLABORADOR'] if collaborators else [])
    return RhToolDecision(acao='buscar_outros_usuarios', filtros=filters), scope


def _pending_people_decision(previous, nr=None, *, require_nr=True):
    """Reuses validated filters; neither history prose nor a model grants access."""
    if not isinstance(previous, dict):
        return None
    try:
        decision = OperationalDecision.model_validate(previous)
        if decision.acao not in {"consultar_grafico_pendencias_nr", "consultar_pendencias_equipe"}:
            return None
        filters = decision.filtros.model_dump()
        selected_nr = nr if nr is not None else filters.get("nr")
        if selected_nr is None and require_nr:
            return "Qual NR você quer detalhar? Por exemplo: Quem tem pendência na NR-6?"
        filters.update(nr=selected_nr, pagina=1, limite=50)
        return OperationalDecision(acao="consultar_pendencias_equipe", filtros=filters,
                                   referencia_listagem_rh=decision.referencia_listagem_rh)
    except (ValueError, TypeError):
        return None


def pending_people_read(message, previous, pending=None, history=()):
    """Drill down a pending-NR chart, or accept a bounded read-only proposal."""
    text = ''.join(c for c in unicodedata.normalize('NFKD', message.casefold()) if not unicodedata.combining(c))
    text = ' '.join(text.split()).strip().rstrip('.!? ')
    text = re.sub(r',? por favor$', '', text).strip()
    # Compatibility with an already approved, immediately preceding question.
    # Only this exact read proposal is recognized, and all filters are revalidated.
    if pending is None and text in {"ok", "isso", "sim", "s", "pode", "pode consultar", "faca isso"}:
        if history and history[-1].get("role") == "assistant":
            legacy = pending_people_offer(history[-1].get("content", ""), previous)
            pending = legacy.model_dump(mode="json") if legacy else None
    if isinstance(pending, dict) and pending.get("acao") == "consultar_pendencias_equipe":
        selection = re.fullmatch(r"(?:nr[ -]?)?(\d{1,3})", text)
        if selection:
            nr = int(selection[1])
            if not 1 <= nr <= 100:
                return "Informe uma NR válida para consultar as pendências."
            return _pending_people_decision(pending, nr)
        if text in {"ok", "isso", "sim", "s", "pode", "pode consultar", "faca isso"}:
            return _pending_people_decision(pending)
        if text in {"nao", "cancelar", "cancele"}:
            return "Tudo bem, não vou realizar essa consulta."
    match = re.fullmatch(
        r"(?:(?:mostre|liste) )?(?:quem|quais (?:pessoas|colaboradores)) "
        r"(?:da (?:minha|nossa|sua) equipe )?(?:tem|tem pendente|esta com|estao com|possui|possuem) "
        r"(?:essa |esta |a |uma )?pendencias?(?: (?:na|da|de|em) nr[ -]?(?P<nr>\d{1,3}))?", text)
    if not match:
        match = re.fullmatch(r"quem e (?:essa|esta|a pessoa|esse|este) (?:da|na) nr[ -]?(?P<nr>\d{1,3})", text)
    if not match:
        return None
    nr = int(match['nr']) if match['nr'] else None
    if nr is not None and not 1 <= nr <= 100:
        return "Informe uma NR válida para consultar as pendências."
    continued = _pending_people_decision(previous, nr)
    if continued is not None:
        return continued
    # A complete team request does not require a previous chart. No identity or
    # company can be supplied by this shortcut; the tool resolves current scope.
    if nr is not None and re.search(r"\b(?:minha|nossa) equipe\b", text):
        return OperationalDecision(acao="consultar_pendencias_equipe", filtros={"nr": nr, "limite": 50})
    return None


def pending_people_scope(previous):
    decision = _pending_people_decision(previous, require_nr=False)
    return decision.model_dump(mode="json") if isinstance(decision, OperationalDecision) else None


def pending_people_offer(answer, previous):
    """Record only this explicit proposal, never arbitrary assistant instructions."""
    text = ''.join(c for c in unicodedata.normalize('NFKD', answer.casefold()) if not unicodedata.combining(c))
    text = ' '.join(text.split()).strip().rstrip('.!? ')
    match = re.fullmatch(r"voce quer que eu consulte quem da sua equipe tem pendencia na nr[ -]?(\d{1,3})", text)
    if not match or not 1 <= int(match[1]) <= 100:
        return None
    decision = _pending_people_decision(previous, int(match[1]))
    return decision if isinstance(decision, OperationalDecision) else None


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


def summary_pdf(message, previous, last_route):
    """Requery a selected summary; never turn historical prose into current evidence."""
    text = ''.join(c for c in unicodedata.normalize('NFKD', message.casefold()) if not unicodedata.combining(c))
    text = ' '.join(text.split()).strip().rstrip('.!? ')
    match = re.fullmatch(
        r"(?:gere|crie|faca|prepare) (?:um )?(?:arquivo )?pdf "
        r"(?:com (?:esse|este|o)|desse|deste|do) (?:resumo|grafico|resultado)"
        r"(?: (?:de |da |dos )?(?P<topic>vencimentos|pendencias|distribuicao da equipe|cobertura))?(?: por favor)?", text)
    if not match:
        return None
    actions = {
        'consultar_grafico_vencimentos': 'vencimentos',
        'consultar_grafico_pendencias_nr': 'pendencias',
        'consultar_indicadores_equipe': 'distribuicao da equipe',
        'consultar_indicadores_cobertura': 'cobertura',
    }
    unclear = 'Consulte primeiro o resumo que deseja exportar nesta conversa. Depois, peça o PDF desse resultado.'
    if last_route != 'consulta_operacional' or not isinstance(previous, dict) or previous.get('acao') not in actions:
        return unclear
    if match['topic'] and match['topic'] != actions[previous['acao']]:
        return unclear
    try:
        decision = OperationalDecision.model_validate(previous)
        filters = decision.filtros.model_dump()
        filters['pagina'] = 1
        # Small fixed summaries need all categories, never the previous visual page.
        if decision.acao != 'consultar_indicadores_cobertura':
            filters['limite'] = 50
        return OperationalDecision(acao=decision.acao, filtros=filters,
            referencia_listagem_rh=decision.referencia_listagem_rh)
    except ValueError:
        return unclear


def change_chart_type(message, previous, last_route):
    """Requery the current chart with the same filters; never count historical rows."""
    from app.modules.chat.chart_menu import chart_style_request
    style = chart_style_request(message)
    text = ''.join(c for c in unicodedata.normalize('NFKD', message.casefold()) if not unicodedata.combining(c))
    text = ' '.join(text.split()).strip().rstrip('.!? ')
    match = re.fullmatch(
        r"(?:agora |ai )?(?:(?:faz|faca|gere|mostre|crie|coloque)(?: isso)? (?:em )?|"
        r"(?:mude|troque|altere)(?: (?:esse|este|o) grafico)? para )"
        r"(?:um )?(?:grafico (?:de )?)?(?P<kind>setores|pizza|rosca|donut|barras|linhas)"
        r"(?:,? por favor)?", text,
    )
    if not match and not style:
        return None
    unclear = "Qual consulta você quer mostrar nesse formato? Consulte os dados desejados nesta conversa e peça a mudança do gráfico."
    if last_route != 'consulta_operacional' or not isinstance(previous, dict):
        return unclear
    kinds = {'setores': 'grafico_setores', 'pizza': 'grafico_setores', 'rosca': 'grafico_rosca',
             'donut': 'grafico_rosca', 'barras': 'grafico_barras', 'linhas': 'grafico_linhas'}
    requested = style or kinds[match['kind']]
    team_actions = {'consultar_indicadores_equipe', 'consultar_grafico_pendencias_nr', 'consultar_grafico_vencimentos'}
    categorical_actions = {'consultar_indicadores_cobertura', 'comparar_conformidade_unidades',
                           'simular_impacto_exigencia_nr', 'consultar_resultados_treinamento'}
    action = previous.get('acao')
    if not isinstance(action, str):
        return unclear
    supported = (set(kinds.values()) if action in team_actions else
                 {'grafico_barras', 'grafico_setores', 'grafico_rosca'} if action == 'consultar_indicadores_cobertura' else
                 {'grafico_barras', 'grafico_setores'} if action in categorical_actions else
                 {'grafico_barras', 'grafico_linhas'} if action == 'consultar_evolucao_conclusoes_equipe' else set())
    if not supported:
        return unclear
    try:
        decision = OperationalDecision.model_validate(previous)
        if requested not in supported:
            return ("Esse formato não é compatível com a consulta anterior. "
                    + ("Para evolução mensal, use linhas ou barras." if action == 'consultar_evolucao_conclusoes_equipe'
                       else "Para essa comparação, use barras ou setores."))
        filters = decision.filtros.model_dump()
        filters.update(tipo_grafico=requested, pagina=1)
        if action in team_actions:
            filters['limite'] = 50
        return OperationalDecision(acao=decision.acao, filtros=filters,
                                   referencia_listagem_rh=decision.referencia_listagem_rh)
    except ValueError:
        return "Não consegui recuperar os filtros do gráfico anterior. Consulte os dados novamente antes de mudar o formato."
