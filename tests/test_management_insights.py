import asyncio
from contextlib import contextmanager
from datetime import date
import json

import pytest
from pydantic import ValidationError

from app.core.security import CurrentUser
from app.modules.operational import insights, tools
from app.modules.chat.schemas import BlocoTabela, BlocoGrafico, BlocoGraficoLinhas


def run(coro):
    return asyncio.run(coro)


def context(role):
    return {"configurable":{"usuario_atual":{"uid":"authenticated-viewer","role":role}}}


def args_for(action):
    if action=="simular_impacto_exigencia_nr":
        return {"nr":10,"cargo":"Analista"}
    if action=="consultar_origem_conformidade":
        return {"conformidade_id":7}
    if action=="consultar_resultados_treinamento":
        return {"evento_id":12}
    return {}


@pytest.mark.parametrize("name",sorted(insights.ACTIONS))
@pytest.mark.parametrize("role",[None,"ADMIN","COLABORADOR","GESTOR"])
def test_role_denials_do_not_access_backends(monkeypatch,name,role):
    if role=="GESTOR" and name!="simular_impacto_exigencia_nr":
        return
    if role=="COLABORADOR" and name in {"consultar_origem_conformidade","consultar_conflitos_agenda"}:
        return
    monkeypatch.setattr(insights,"sql_read",lambda *a:pytest.fail("Forbidden SQL"))
    async def forbidden(*a):
        pytest.fail("Forbidden HTTP")
    monkeypatch.setattr(insights,"read_api",forbidden)
    result=run(tools.REGISTRY[name].ainvoke(args_for(name),config=context(role) if role else {}))
    assert result["status"]=="nao_autorizado"


@pytest.mark.parametrize("name",sorted(insights.ACTIONS-{"consultar_resultados_treinamento"}))
def test_langchain_injects_authenticated_config_and_routes_args(monkeypatch,name):
    calls=[]
    def read(action,args,user):
        calls.append((action,args,user))
        return {"status":"sem_dados"}
    monkeypatch.setattr(insights,"sql_read",read)
    result=run(tools.REGISTRY[name].ainvoke(args_for(name),config=context("GESTOR_WORKSPACE")))
    assert result["status"]=="sem_dados"
    assert len(calls)==1
    assert calls[0][0]==name and calls[0][2].uid=="authenticated-viewer"
    decision=tools.OperationalDecision.model_validate({"acao":name,"filtros":args_for(name)})
    assert isinstance(decision.filtros,insights.InsightArgs)
    schema=tools.REGISTRY[name].args_schema.model_json_schema()["properties"]
    assert not {"uid","role","config","workspace_id","usuario_id"} & schema.keys()


@pytest.mark.parametrize("payload",[{"uid":"other"},{"role":"GESTOR_WORKSPACE"},{"workspace_id":1},
    {"evento_id":-1},{"limite":51},{"pagina":0},{"nr":0},
    {"inicio":"2026-10-01"},{"inicio":"2026-10-31","fim":"2026-10-01"},
    {"inicio":"2020-01-01","fim":"2026-01-01"}])
def test_invalid_arguments_rejected(payload):
    with pytest.raises(ValidationError):
        insights.InsightArgs.model_validate(payload)


@pytest.mark.parametrize("name,filters",[
    ("consultar_matriz_conformidade_equipe",{"evento_id":4}),
    ("consultar_evidencias_pendentes_validacao",{"cargo":"Analista"}),
    ("consultar_resultados_treinamento",{"evento_id":1,"nome":"Pessoa"}),
    ("consultar_origem_conformidade",{}),
    ("simular_impacto_exigencia_nr",{"nr":10}),
    ("consultar_resultados_treinamento",{}),
    ("simular_impacto_exigencia_nr",{"nr":10,"cargo":"Analista","tipo_grafico":"grafico_linhas"}),
])
def test_missing_or_unsupported_filters_clarify_before_io(monkeypatch,name,filters):
    monkeypatch.setattr(insights,"sql_read",lambda *a:pytest.fail("No SQL before clarification"))
    async def forbidden(*a):pytest.fail("No HTTP before clarification")
    monkeypatch.setattr(insights,"read_api",forbidden)
    assert run(tools.REGISTRY[name].ainvoke(filters,config=context("GESTOR_WORKSPACE")))["status"]=="esclarecer"


@pytest.mark.parametrize("name",["consultar_conflitos_agenda","consultar_origem_conformidade"])
@pytest.mark.parametrize("filters",[{"escopo":"equipe"},{"nome":"Outra Pessoa"}])
def test_collaborator_cannot_select_other_people(monkeypatch,name,filters):
    monkeypatch.setattr(insights,"sql_read",lambda *a:pytest.fail("Selector must not reach SQL"))
    payload={**args_for(name),**filters}
    assert run(tools.REGISTRY[name].ainvoke(payload,config=context("COLABORADOR")))["status"]=="esclarecer"


@pytest.mark.parametrize("role",["GESTOR","GESTOR_WORKSPACE"])
@pytest.mark.parametrize("action",sorted(insights.ACTIONS-{"consultar_resultados_treinamento"}))
def test_sql_revalidates_scope_and_parameterizes_selectors(role,action):
    filters=args_for(action)
    if action not in {"consultar_evidencias_pendentes_validacao","simular_impacto_exigencia_nr"}:
        filters.update(escopo="equipe",nome="Pessoa%_' OR TRUE --")
    user=CurrentUser(uid="authenticated-viewer",role=role)
    count,query,params,cols,*_=insights.build_query(action,insights.InsightArgs(**filters),user)
    assert query.count("%s")==len(params)+2
    assert count.count("%s")==len(params)
    assert "authenticated-viewer" not in query and "OR TRUE" not in query
    assert "authenticated-viewer" in params
    assert "ORDER BY" in query and query.endswith("LIMIT %s OFFSET %s")
    assert "'ATIVO'" in query and "tipo=%s" in query or "viewer.tipo = %s" in query
    if action=="consultar_evidencias_pendentes_validacao":
        assert "e.gestor_id=v.id_usuario" in query
        assert "ce.status='PENDENTE'" in query and "e.modo_conclusao='COLABORADOR'" in query
        assert "caminho_objeto" not in query
    else:
        assert "unit.workspace_id = viewer_unit.workspace_id" in query
        assert "fn_seleciona_unidades" in query
        assert "viewer.tipo = 'GESTOR_WORKSPACE' OR target.unidade_id = viewer.unidade_id" in query


def test_own_queries_use_uid_not_model_person_selector():
    user=CurrentUser(uid="owner",role="COLABORADOR")
    for action in ["consultar_conflitos_agenda","consultar_origem_conformidade"]:
        _,query,params,*_=insights.build_query(action,insights.InsightArgs(**args_for(action)),user)
        assert "target.firebase_uid=%s" in query and "target.tipo=%s" in query
        assert params[:2]==["owner","COLABORADOR"]


def test_coverage_uses_same_requirements_and_distinguishes_submission():
    user=CurrentUser(uid="viewer",role="GESTOR")
    _,matrix,_,*_=insights.build_query("consultar_matriz_conformidade_equipe",insights.InsightArgs(),user)
    _,coverage,params,*_=insights.build_query("consultar_cobertura_treinamentos",insights.InsightArgs(nr=10),user)
    for query in (matrix,coverage):
        assert "JOIN cargo_nr" in query and "JOIN unidade_nr" in query
        assert "n.revogada=FALSE" in query and "aplicavel IS TRUE" in query
        assert "'SEM_VALIDADE'" in query and "'SEM_REGISTRO'" in query
        assert "ce.status='REJEITADO'" in query and "ce.status='PENDENTE'" in query
    assert "NOT (agendado OR em_analise)" in coverage
    assert params[-1] is False


def test_conflicts_pair_once_and_exclude_touching_boundaries():
    _,query,_,*_=insights.build_query("consultar_conflitos_agenda",insights.InsightArgs(),CurrentUser(uid="owner",role="GESTOR"))
    assert "a.id_turma<b.id_turma" in query
    assert "a.data_inicial<b.data_termino AND b.data_inicial<a.data_termino" in query
    assert "a.id_usuario=b.id_usuario" in query
    assert "e.status='ATIVO'" in query


def test_simulation_only_reads_existing_applicable_records():
    count,query,params,*_=insights.build_query("simular_impacto_exigencia_nr",insights.InsightArgs(nr=10,cargo="Analista"),CurrentUser(uid="owner",role="GESTOR_WORKSPACE"))
    assert "aplicavel IS TRUE" in query and "n.revogada=FALSE" in query
    assert "ja_aplicavel" in query and params[-2:]==[10,10]
    assert "LOWER(cargo.nome)=LOWER(%s)" in query and "Analista" in params
    assert not any(word in query.upper() for word in ["UPDATE ","DELETE ","INSERT ","CALL "])


def test_sql_snapshot_count_survives_empty_page_and_sanitizes_errors(monkeypatch):
    monkeypatch.setattr(insights.app_config,"DATABASE_URL","postgresql://fake")
    calls=[]
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,q,p):calls.append((q,p))
        def fetchone(self):return (83,)
        def fetchall(self):return []
    class Conn:
        def cursor(self):return Cursor()
    @contextmanager
    def conn(*args):yield Conn()
    monkeypatch.setattr(insights,"read_only_connection",conn)
    result=insights.sql_read("consultar_matriz_conformidade_equipe",insights.InsightArgs(pagina=4),CurrentUser(uid="viewer",role="GESTOR"))
    assert result["status"]=="sem_dados" and result["total"]==83
    assert calls[0][0]=="BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert calls[-1][1][-2:]==[20,60]
    def failure(*args):raise RuntimeError("sensitive-driver-message")
    monkeypatch.setattr(insights,"read_only_connection",failure)
    result=insights.sql_read("consultar_matriz_conformidade_equipe",insights.InsightArgs(),CurrentUser(uid="viewer",role="GESTOR"))
    assert result["status"]=="indisponivel" and "sensitive" not in str(result)


def test_event_results_aggregate_full_java_response_and_drop_private_fields(monkeypatch):
    async def api(path,params):
        assert path=="/events/12/participants" and params=={}
        return {"status":"ok","data":[{"participantId":1,"participationStatus":"CONCLUIDO","profilePhotoUrl":"secret-url"},
            {"participantId":1,"participationStatus":"CONCLUIDO"},{"participantId":2,"participationStatus":"EM_ANALISE"}]}
    monkeypatch.setattr(insights,"read_api",api)
    result=run(tools.REGISTRY["consultar_resultados_treinamento"].ainvoke({"evento_id":12},config=context("GESTOR")))
    assert result["status"]=="ok"
    completed=next(row for row in result["linhas"] if row["categoria"]=="CONCLUIDO")
    assert completed["quantidade"]==2 and completed["pessoas_unicas"]==1
    assert sum(row["valor"] for row in result["grafico"])==3
    assert "secret-url" not in str(result) and "participantId" not in str(result)
    BlocoTabela(titulo=result["titulo"],colunas=result["colunas"],linhas=result["linhas"],pagina=1,total_paginas=1,total=4)


@pytest.mark.parametrize("response",[{"status":"nao_autorizado","mensagem":"Negado"},
    {"status":"indisponivel"},{"status":"sem_dados"}])
def test_event_authorization_and_failures_propagate(monkeypatch,response):
    async def api(*a):return response
    monkeypatch.setattr(insights,"read_api",api)
    assert run(insights.event_results(insights.InsightArgs(evento_id=1)))==response


@pytest.mark.parametrize("data",[{},[{"participantId":1,"participationStatus":"UNKNOWN"}],
    [{"participantId":True,"participationStatus":"PENDENTE"}]])
def test_event_unknown_status_or_invalid_shape_not_guessed(monkeypatch,data):
    async def api(*a):return {"status":"ok","data":data}
    monkeypatch.setattr(insights,"read_api",api)
    assert run(insights.event_results(insights.InsightArgs(evento_id=1)))["status"]=="indisponivel"


@pytest.mark.parametrize("question",[
    "Mostre a matriz de conformidade da equipe", "Quem precisa da NR-10 e não está em nenhum treinamento?",
    "Quais evidências aguardam validação?", "Como foi o treinamento de NR-10?",
    "Mostre a evolução das conclusões da equipe", "Tenho treinamentos no mesmo horário?",
    "Se exigirmos NR-35 neste cargo, qual o impacto?", "De onde veio esta conformidade?",
])
def test_requests_are_routed_to_operational_decision(question):
    assert tools.is_operational_request(question)


@pytest.mark.parametrize("action",sorted(insights.ACTIONS-{"consultar_resultados_treinamento"}))
def test_every_sql_tool_has_bounded_valid_positive_result(monkeypatch,action):
    from app.modules.chat.schemas import BlocoTabela
    user=CurrentUser(uid="viewer",role="GESTOR_WORKSPACE")
    args=insights.InsightArgs(**args_for(action))
    _,_,_,columns,*_=insights.build_query(action,args,user)
    values={"nome":"Pessoa Teste","unidade":"Unidade","nr":10,"situacao":"SEM_REGISTRO",
            "conformidade_id":None,"validade":None,"agendado":False,"em_analise":False,
            "referencia":7,"evento_id":12,"evento":"Evento","turma":"Turma","envio":None,"arquivos":0,
            "categoria":"2026-10" if action=="consultar_evolucao_conclusoes_equipe" else "VIGENTE",
            "quantidade":2,"ja_aplicavel":1,"evento_a":"Evento A","evento_b":"Evento B",
            "inicio":"2026-10-31T10:00:00","fim":"2026-10-31T11:00:00",
            "origem":"EVENTO","aplicavel":True,"conclusao":9,"formulario":None}
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,q,p):assert q.count("%s")==len(p)
        def fetchone(self):return (1,)
        def fetchall(self):return [tuple(values[key] for key,_ in columns)]
    class Conn:
        def cursor(self):return Cursor()
    @contextmanager
    def conn(*a):yield Conn()
    monkeypatch.setattr(insights.app_config,"DATABASE_URL","postgresql://fake")
    monkeypatch.setattr(insights,"read_only_connection",conn)
    result=run(tools.REGISTRY[action].ainvoke(args.model_dump(),config=context("GESTOR_WORKSPACE")))
    assert result["status"]=="ok" and result["total"]==1
    BlocoTabela(titulo=result["titulo"],colunas=result["colunas"],linhas=result["linhas"],pagina=1,total_paginas=1,total=1)
    if result.get("grafico"):
        cls=BlocoGraficoLinhas if action=="consultar_evolucao_conclusoes_equipe" else BlocoGrafico
        cls(titulo=result["titulo"],fonte=result["fonte"],unidade=result["unidade_grafico"],dados=result["grafico"])


def test_monthly_chart_defaults_to_lines_and_preserves_explicit_bars():
    decision=tools.OperationalDecision.model_validate({"acao":"consultar_evolucao_conclusoes_equipe","filtros":{}})
    assert decision.filtros.tipo_grafico=="grafico_linhas"
    decision=tools.OperationalDecision.model_validate({"acao":"consultar_evolucao_conclusoes_equipe","filtros":{"tipo_grafico":"grafico_barras"}})
    assert decision.filtros.tipo_grafico=="grafico_barras"
