import asyncio
import json
from contextlib import contextmanager

import httpx
import pytest
from pydantic import ValidationError
from app.infrastructure import astro_queries
from app.modules.operational import tools


def run(coro):
    return asyncio.run(coro)


def context(role):
    return {"configurable":{"usuario_atual":{"uid":"viewer", "role":role}}}


@pytest.mark.parametrize("name", list(tools.REGISTRY))
@pytest.mark.parametrize("role", [None, "ADMIN", "COLABORADOR"])
def test_profile_denials_never_query_backends(monkeypatch,name,role):
    if role == "COLABORADOR" and name == "consultar_detalhes_evento":
        return
    async def forbidden(*a,**kw):
        pytest.fail("Backend called for denied profile")
    monkeypatch.setattr(tools, "read_api", forbidden)
    monkeypatch.setattr(tools, "team_read", lambda *a: pytest.fail("SQL called for denied profile"))
    result = run(tools.REGISTRY[name].ainvoke({},config=context(role) if role else {}))
    assert result["status"] == "nao_autorizado"


@pytest.mark.parametrize("role", ["GESTOR", "GESTOR_WORKSPACE"])
@pytest.mark.parametrize("name", list(tools.REGISTRY))
def test_manager_can_reach_only_registered_read_backend(monkeypatch,name,role):
    calls = []
    async def fake_api(path,params):
        calls.append((path,params))
        return {"status":"sem_dados","mensagem":"Nada no escopo"}
    def fake_team(action,args,user):
        calls.append((action,user.role))
        return {"status":"sem_dados"}
    monkeypatch.setattr(tools,"read_api",fake_api)
    monkeypatch.setattr(tools,"team_read",fake_team)
    args = {"identificador":"94229143-d20b-4766-80a4-05341435c236" if name == "consultar_respostas_formulario" else "12"} if name not in tools.TEAM_ACTIONS else {}
    result = run(tools.REGISTRY[name].ainvoke(args,config=context(role)))
    assert result["status"] == "sem_dados"
    assert len(calls) == 1


@pytest.mark.parametrize("role,path", [("COLABORADOR","/events/collaborator/12"),("GESTOR","/events/12")])
def test_event_details_use_correct_java_scope(monkeypatch,role,path):
    async def fake_api(actual,params):
        assert actual == path
        return {"status":"nao_autorizado","mensagem":"Negado"}
    monkeypatch.setattr(tools,"read_api",fake_api)
    assert run(tools.consultar_detalhes_evento.ainvoke({"identificador":"12"},config=context(role)))["status"] == "nao_autorizado"


@pytest.mark.parametrize("payload", [
    {"identificador":"../../admin"}, {"identificador":"https://evil.test"},
    {"uid":"other"}, {"role":"GESTOR_WORKSPACE"}, {"limite":51}, {"pagina":0},
])
def test_untrusted_arguments_rejected(payload):
    with pytest.raises(ValidationError):
        tools.ApiArgs.model_validate(payload)


@pytest.mark.parametrize("role",["GESTOR","GESTOR_WORKSPACE"])
def test_sql_aggregates_before_pagination_and_revalidates_scope(monkeypatch,role):
    monkeypatch.setattr(tools.app_config,"DATABASE_URL","postgresql://fake")
    calls = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def execute(self,sql,params): calls.append((sql,params))
        def fetchall(self): return [("Técnica",7,2),("Operador",4,2)]
    class Conn:
        def cursor(self): return Cursor()
    @contextmanager
    def fake_conn(dsn): yield Conn()
    monkeypatch.setattr(tools,"read_only_connection",fake_conn)
    result = run(tools.consultar_indicadores_equipe.ainvoke({},config=context(role)))
    sql,params = calls[0]
    assert sql.index("GROUP BY") < sql.index("LIMIT")
    assert "viewer.status = 'ATIVO'" in sql
    assert "unit.workspace_id = viewer_unit.workspace_id" in sql
    assert "target.unidade_id = viewer.unidade_id" in sql
    assert "fn_seleciona_unidades(%s)" in sql
    assert params[:3] == ["viewer",role,"viewer"]
    assert result["grafico"] == [{"categoria":"Técnica","valor":7},{"categoria":"Operador","valor":4}]
    assert result["completo"] is True


def test_partial_aggregation_never_emits_chart(monkeypatch):
    monkeypatch.setattr(tools.app_config,"DATABASE_URL","postgresql://fake")
    class Cursor:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def execute(self,*a): pass
        def fetchall(self): return [("Cargo",7,30)]
    class Conn:
        def cursor(self): return Cursor()
    @contextmanager
    def fake_conn(dsn): yield Conn()
    monkeypatch.setattr(tools,"read_only_connection",fake_conn)
    result = run(tools.consultar_indicadores_equipe.ainvoke({},config=context("GESTOR")))
    assert result["total"] == 30 and not result["completo"]
    assert "grafico" not in result


def test_form_response_projection_does_not_expose_answers_or_location(monkeypatch):
    async def fake_api(*a):
        return {"status":"ok","data":{"form":{"questions":[{"question":"Pergunta","type":"TEXT","answer":["segredo"]}]},"locations":[{"latitude":-20}],"canReview":True}}
    monkeypatch.setattr(tools,"read_api",fake_api)
    result = run(tools.consultar_respostas_formulario.ainvoke({"identificador":"94229143-d20b-4766-80a4-05341435c236"},config=context("GESTOR")))
    assert result["linhas"][0]["respondida"] == "Sim"
    assert "segredo" not in json.dumps(result) and "latitude" not in json.dumps(result)


def test_missing_record_requests_clarification_without_api(monkeypatch):
    async def forbidden(*a): pytest.fail("Unexpected API read")
    monkeypatch.setattr(tools,"read_api",forbidden)
    assert run(tools.consultar_detalhes_evento.ainvoke({},config=context("GESTOR")))["status"] == "esclarecer"


def test_gateway_uses_request_token_and_does_not_follow_redirects(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(302,headers={"Location":"https://evil.test"})
    original = httpx.AsyncClient
    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(handler),**kwargs)
    monkeypatch.setattr(astro_queries.httpx,"AsyncClient",client)
    monkeypatch.setattr(astro_queries.config,"ASTRO_API_BASE_URL","https://api.astro-app.solutions")
    handle = astro_queries.request_token.set("test-private-token")
    try:
        result = run(astro_queries.read_api("/forms/received"))
    finally:
        astro_queries.request_token.reset(handle)
    assert result["status"] == "indisponivel"
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer test-private-token"
    assert "test-private-token" not in json.dumps(result)
    assert astro_queries.request_token.get() is None


@pytest.mark.parametrize("status,expected", [(400,"esclarecer"),(422,"esclarecer"),(401,"nao_autorizado"),(403,"nao_autorizado"),(404,"sem_dados"),(429,"indisponivel"),(500,"indisponivel")])
def test_gateway_errors_are_bounded_and_sanitized(monkeypatch,status,expected):
    original = httpx.AsyncClient
    monkeypatch.setattr(astro_queries.httpx,"AsyncClient",lambda **kw: original(transport=httpx.MockTransport(lambda r:httpx.Response(status,text="private backend details")),**kw))
    monkeypatch.setattr(astro_queries.config,"ASTRO_API_BASE_URL","https://api.astro-app.solutions")
    handle = astro_queries.request_token.set("test-token")
    try: result = run(astro_queries.read_api("/forms/received"))
    finally: astro_queries.request_token.reset(handle)
    assert result["status"] == expected
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize("message,route,expected", [
    ("gere um gráfico com base nesses", "rh", True),
    ("gráfico dos meus treinamentos", "rh", False),
    ("meus próximos eventos", "rh", False),
    ("distribuição dos funcionários por cargo", "", True),
    ("Quem precisa renovar a NR-10?", "", True),
    ("Quais formulários ainda preciso responder?", "", True),
    ("Mostre a ficha desse treinamento", "", True),
])
def test_operational_intents_preserve_personal_queries(message,route,expected):
    assert tools.is_operational_request(message,route) is expected


def test_candidate_event_nr_is_derived_from_authorized_event(monkeypatch):
    calls = []
    async def fake_api(path,params):
        calls.append((path,params))
        if path == "/events/12": return {"status":"ok","data":{"nrId":10}}
        assert path == "/events/eligible-candidates" and params == {"nrId":10}
        return {"status":"ok","data":[{"name":"Ana","email":"ana@example.com","roleName":"Técnica","unitName":"Matriz"}]}
    monkeypatch.setattr(tools,"read_api",fake_api)
    result = run(tools.consultar_candidatos_treinamento.ainvoke({"identificador":"12"},config=context("GESTOR")))
    assert result["status"] == "ok" and len(calls) == 2
    assert result["linhas"][0]["name"] == "Ana"


@pytest.mark.parametrize("action",["consultar_vencimentos_conformidades","consultar_pendencias_equipe"])
def test_team_queries_apply_nr_and_window_inside_sql(monkeypatch,action):
    monkeypatch.setattr(tools.app_config,"DATABASE_URL","postgresql://fake")
    captured = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def execute(self,sql,params): captured.append((sql,params))
        def fetchall(self): return []
    class Conn:
        def cursor(self): return Cursor()
    @contextmanager
    def fake_conn(dsn): yield Conn()
    monkeypatch.setattr(tools,"read_only_connection",fake_conn)
    result = run(tools.REGISTRY[action].ainvoke({"nr":10,"dias":30},config=context("GESTOR")))
    sql,params = captured[0]
    assert params[-2:] == [20,0] and params.count(10) == 2
    assert result["status"] == "sem_dados" and "outras páginas" in result["mensagem"]
    if action == "consultar_vencimentos_conformidades":
        assert "v.data_validade IS NOT NULL" in sql and "CURRENT_DATE + %s" in sql
    else:
        assert "e.status <> 'CANCELADO'" in sql and "nr.revogada = FALSE" in sql
        assert "c.aplicavel IS TRUE" in sql and "NULLS LAST" in sql


@pytest.mark.parametrize("action,data", [
    ("consultar_conformidade_unidade",[{"unitName":"Matriz","nrId":10,"nrTitle":"NR-10","expiresAt":"2026-11-20","status":"VIGENTE","origin":"MANUAL"}]),
    ("consultar_formularios_pendentes",{"items":[{"id":"abc","title":"Inspeção","dueDate":"2026-11-20","status":"PENDENTE","questionCount":2}],"totalElements":1}),
    ("consultar_detalhes_evento",{"title":"Treinamento","description":"Detalhes","status":"ATIVO","nrId":10,"completionMode":"MANUAL","mandatoryEvidence":False,"eventClasses":[]}),
])
def test_java_fields_produce_valid_public_table_contract(monkeypatch,action,data):
    from app.modules.chat.schemas import BlocoTabela
    async def fake_api(*args): return {"status":"ok","data":data}
    monkeypatch.setattr(tools,"read_api",fake_api)
    result = run(tools.REGISTRY[action].ainvoke({"identificador":"12"},config=context("GESTOR")))
    assert result["status"] == "ok"
    table = BlocoTabela(titulo=result["titulo"], colunas=result["colunas"], linhas=result["linhas"], pagina=result["pagina"], total_paginas=result["total_paginas"], total=result["total"])
    assert table.linhas


def test_team_scope_includes_pre_registered_people_without_firebase_uid(monkeypatch):
    monkeypatch.setattr(tools.app_config,"DATABASE_URL","postgresql://fake")
    class Cursor:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def execute(self,sql,params):
            assert "target.id_usuario <> viewer.id_usuario" in sql
            assert "target.firebase_uid <> viewer.firebase_uid" not in sql
            assert "viewer.firebase_uid = %s" in sql
        def fetchall(self): return [("Técnica",2,1)]
    class Conn:
        def cursor(self): return Cursor()
    @contextmanager
    def fake_conn(dsn): yield Conn()
    monkeypatch.setattr(tools,"read_only_connection",fake_conn)
    result = run(tools.consultar_indicadores_equipe.ainvoke({},config=context("GESTOR")))
    assert result["grafico"][0]["valor"] == 2



def test_invalid_form_uuid_does_not_reach_java(monkeypatch):
    async def forbidden(*a): pytest.fail("Invalid UUID reached backend")
    monkeypatch.setattr(tools,"read_api",forbidden)
    result = run(tools.consultar_respostas_formulario.ainvoke({"identificador":"000000000000000000000000"},config=context("GESTOR")))
    assert result["status"] == "esclarecer"


@pytest.mark.parametrize("kind",["grafico_barras","grafico_setores"])
def test_team_chart_preserves_requested_type(monkeypatch,kind):
    monkeypatch.setattr(tools.app_config,"DATABASE_URL","postgresql://fake")
    class Cursor:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def execute(self,*a): pass
        def fetchall(self): return [("ATIVO",10,1)]
    class Conn:
        def cursor(self): return Cursor()
    @contextmanager
    def fake_conn(dsn): yield Conn()
    monkeypatch.setattr(tools,"read_only_connection",fake_conn)
    result = run(tools.consultar_indicadores_equipe.ainvoke({"tipo_grafico":kind},config=context("GESTOR")))
    assert result["tipo_grafico"] == kind
    assert result["grafico"] == [{"categoria":"ATIVO","valor":10}]


def test_team_lines_does_not_invent_time_series(monkeypatch):
    monkeypatch.setattr(tools,"read_only_connection",lambda *a: pytest.fail("No historical query exists"))
    result = run(tools.consultar_indicadores_equipe.ainvoke({"tipo_grafico":"grafico_linhas"},config=context("GESTOR")))
    assert result["status"] == "esclarecer"
    assert "sem série histórica" in result["mensagem"]
    assert "grafico" not in result


@pytest.mark.parametrize("action,kind,unit",[("consultar_grafico_pendencias_nr","grafico_barras","pendências"),("consultar_grafico_vencimentos","grafico_setores","registros")])
def test_risk_charts_aggregate_before_pagination_and_keep_units(monkeypatch,action,kind,unit):
    from app.modules.chat.schemas import BlocoTabela, BlocoGraficoSetores
    monkeypatch.setattr(tools.app_config,"DATABASE_URL","postgresql://fake")
    class Cursor:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def execute(self,sql,params):
            assert sql.index("COUNT(DISTINCT") < sql.index("LIMIT %s OFFSET")
            assert "target.id_usuario <> viewer.id_usuario" in sql
            assert "unit.workspace_id = viewer_unit.workspace_id" in sql
            assert params[-2:] == [20,0]
            if action == "consultar_grafico_pendencias_nr":
                assert "WHERE situacao <> 'VIGENTE' GROUP BY nr" in sql
                assert "target.status = 'ATIVO'" in sql
                assert "c.aplicavel IS TRUE" in sql
            else:
                assert "v.data_validade IS NULL THEN 5" in sql
                assert sql.index("CURRENT_DATE + 7") < sql.index("CURRENT_DATE + 30")
                assert "LEFT JOIN records" in sql
        def fetchall(self): return [("NR-10" if unit == "pendências" else "Vencidas",5,3,"2026-10-30",1)]
    class Conn:
        def cursor(self): return Cursor()
    @contextmanager
    def fake_conn(dsn): yield Conn()
    monkeypatch.setattr(tools,"read_only_connection",fake_conn)
    result=run(tools.REGISTRY[action].ainvoke({"tipo_grafico":kind},config=context("GESTOR")))
    assert result["unidade_grafico"] == unit and result["tipo_grafico"] == kind
    assert result["linhas"][0]["pessoas_unicas"] == 3
    assert result["grafico"][0]["valor"] == 5
    assert "não some" in result["aviso"]
    BlocoTabela(titulo=result["titulo"],colunas=result["colunas"],linhas=result["linhas"],total=result["total"],pagina=1,total_paginas=1)
    BlocoGraficoSetores(titulo=result["titulo"],dados=result["grafico"],unidade=unit,fonte=result["fonte"])


@pytest.mark.parametrize("action",["consultar_grafico_pendencias_nr","consultar_grafico_vencimentos"])
def test_risk_chart_lines_requires_history(monkeypatch,action):
    monkeypatch.setattr(tools,"read_only_connection",lambda *a: pytest.fail("No time series exists"))
    assert run(tools.REGISTRY[action].ainvoke({"tipo_grafico":"grafico_linhas"},config=context("GESTOR")))["status"] == "esclarecer"


def test_fixed_expiry_buckets_do_not_ignore_custom_window(monkeypatch):
    monkeypatch.setattr(tools,"read_only_connection",lambda *a: pytest.fail("Custom range must not be ignored"))
    assert run(tools.consultar_grafico_vencimentos.ainvoke({"dias":60},config=context("GESTOR")))["status"] == "esclarecer"
